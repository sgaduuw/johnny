"""FQDN convergence ladder, ported from the callback plugin.

Source of truth: sgaduuw/johnny-callback `plugins/callback/callback.py`,
functions `_resolve_fqdn` and `_ensure_ansible_prefix`. Ported
verbatim from commit 812ed8a.

Drift between this copy and the plugin is caught by
`tests/test_fqdn_resolver.py::TestResolveFqdnMatchesCallbackPlugin`,
which hard-codes the same input table the plugin's own tests use.
The plugin declares a hard "zero runtime deps" rule (see
johnny-callback/CLAUDE.md), so a shared package isn't an option;
copy + sync-test is the agreed contract.
"""

from __future__ import annotations


def ensure_ansible_prefix(facts: dict) -> dict:
    """Re-add the ansible_ prefix to fact keys that lost it.

    Ansible strips the ansible_ prefix when merging gathered facts
    into host_vars["ansible_facts"]. The plugin's _snapshot_play_facts
    code path between v0.1.2 and v0.1.4 inclusive read those vars
    without re-prefixing, so historical fact snapshots stored in
    johnny may carry the unprefixed shape (fqdn, hostname,
    default_ipv4, ...). Run them through this helper before
    resolve_fqdn or any other consumer that expects the prefixed
    contract (e.g. the dedup tool reading host_facts_history).
    """
    out: dict = {}
    for k, v in facts.items():
        out[k if k.startswith("ansible_") else f"ansible_{k}"] = v
    return out


def resolve_fqdn(facts: dict, inventory_hostname: str) -> str:
    """Resolve to the most-qualified hostname the facts can produce.

    Convergence ladder. The same physical host should land at the
    same key whether facts arrived via fresh setup (full ansible_fqdn
    populated), a smart-cache snapshot (may carry only ansible_hostname
    + ansible_domain), or a sparse subset gather:

    1. ansible_fqdn if it contains a dot (skips bare "localhost").
    2. ansible_hostname + "." + ansible_domain if both are non-empty.
    3. ansible_nodename if it contains a dot.
    4. inventory_hostname (may itself be unqualified; user discipline).

    Caller is responsible for passing prefixed keys; pre-normalise via
    ensure_ansible_prefix when reading from sources known to strip
    the prefix.
    """
    fqdn = facts.get("ansible_fqdn")
    if fqdn and "." in fqdn:
        return fqdn
    hostname = facts.get("ansible_hostname")
    domain = facts.get("ansible_domain")
    if hostname and domain:
        return f"{hostname}.{domain}"
    nodename = facts.get("ansible_nodename")
    if nodename and "." in nodename:
        return nodename
    return inventory_hostname
