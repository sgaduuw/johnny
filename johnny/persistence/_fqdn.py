"""FQDN convergence ladder, ported from the callback plugin.

Source of truth: sgaduuw/johnny-callback `plugins/callback/callback.py`,
function `_resolve_fqdn`. Ported verbatim from commit 4d46bee.

Drift between this copy and the plugin is caught by
`tests/test_fqdn_resolver.py::TestResolveFqdnMatchesCallbackPlugin`,
which hard-codes the same input table the plugin's own tests use.
The plugin declares a hard "zero runtime deps" rule (see
johnny-callback/CLAUDE.md), so a shared package isn't an option;
copy + sync-test is the agreed contract.
"""

from __future__ import annotations


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
