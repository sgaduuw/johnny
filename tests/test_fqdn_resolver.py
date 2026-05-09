"""Tests for johnny.persistence._fqdn.resolve_fqdn.

The function is a verbatim port of the same-named helper in the
johnny-callback plugin (see plugins/callback/callback.py:99 in that
repo). If this file's expected outputs ever diverge from the
plugin's tests, one side has drifted: bump the matching side or
the convergence guarantee breaks.
"""

from __future__ import annotations

from johnny.persistence._fqdn import ensure_ansible_prefix, resolve_fqdn


class TestEnsureAnsiblePrefix:
    """Mirror of johnny-callback's TestEnsureAnsiblePrefix class."""

    def test_re_adds_prefix_to_unprefixed_keys(self) -> None:
        out = ensure_ansible_prefix({
            "fqdn": "web1.example.com",
            "default_ipv4": {"address": "10.0.0.1"},
            "memtotal_mb": 4096,
        })
        assert out == {
            "ansible_fqdn": "web1.example.com",
            "ansible_default_ipv4": {"address": "10.0.0.1"},
            "ansible_memtotal_mb": 4096,
        }

    def test_keeps_already_prefixed_keys(self) -> None:
        out = ensure_ansible_prefix({
            "ansible_fqdn": "web1.example.com",
            "ansible_uptime_seconds": 3600,
        })
        assert out == {
            "ansible_fqdn": "web1.example.com",
            "ansible_uptime_seconds": 3600,
        }

    def test_handles_mixed_keys(self) -> None:
        out = ensure_ansible_prefix({
            "fqdn": "x",
            "ansible_distribution": "Debian",
        })
        assert out == {
            "ansible_fqdn": "x",
            "ansible_distribution": "Debian",
        }

    def test_empty_input(self) -> None:
        assert ensure_ansible_prefix({}) == {}


class TestResolveFqdnLadder:
    """Mirror of johnny-callback's TestResolveFqdn class."""

    def test_prefers_ansible_fqdn_when_dotted(self) -> None:
        f = {"ansible_fqdn": "host.example.com", "ansible_nodename": "host"}
        assert resolve_fqdn(f, "inv-name") == "host.example.com"

    def test_skips_bare_ansible_fqdn(self) -> None:
        f = {
            "ansible_fqdn": "localhost",
            "ansible_hostname": "web1",
            "ansible_domain": "example.com",
        }
        assert resolve_fqdn(f, "inv-name") == "web1.example.com"

    def test_combines_hostname_and_domain(self) -> None:
        f = {"ansible_hostname": "web1", "ansible_domain": "example.com"}
        assert resolve_fqdn(f, "inv-name") == "web1.example.com"

    def test_skips_hostname_when_domain_empty(self) -> None:
        f = {"ansible_hostname": "web1", "ansible_domain": ""}
        assert resolve_fqdn(f, "inv-name") == "inv-name"

    def test_accepts_dotted_nodename(self) -> None:
        f = {"ansible_nodename": "host.example.com"}
        assert resolve_fqdn(f, "inv-name") == "host.example.com"

    def test_skips_undotted_nodename(self) -> None:
        f = {"ansible_nodename": "host"}
        assert resolve_fqdn(f, "inv-name") == "inv-name"

    def test_falls_back_to_inventory_hostname(self) -> None:
        assert resolve_fqdn({}, "inv-name") == "inv-name"

    def test_ladder_priority_fqdn_over_hostname_domain(self) -> None:
        f = {
            "ansible_fqdn": "web1.example.com",
            "ansible_hostname": "web1",
            "ansible_domain": "different.example.com",
        }
        assert resolve_fqdn(f, "inv-name") == "web1.example.com"


class TestResolveFqdnMatchesCallbackPlugin:
    """Drift catcher.

    The callback plugin owns the canonical _resolve_fqdn /
    _ensure_ansible_prefix. This johnny copy must agree on every
    input the plugin tests. If a row here fails, the two
    implementations have drifted; one of them is behind. Update the
    lagging side, do not relax the assertion.

    Plugin source pinned at:
      sgaduuw/johnny-callback @ 812ed8a :: plugins/callback/callback.py
    """

    CASES = [
        # (facts, inventory_hostname, expected_canonical)
        (
            {"ansible_fqdn": "host.example.com", "ansible_nodename": "host"},
            "inv-name",
            "host.example.com",
        ),
        (
            {
                "ansible_fqdn": "localhost",
                "ansible_hostname": "web1",
                "ansible_domain": "example.com",
            },
            "inv-name",
            "web1.example.com",
        ),
        (
            {"ansible_hostname": "web1", "ansible_domain": "example.com"},
            "inv-name",
            "web1.example.com",
        ),
        (
            {"ansible_hostname": "web1", "ansible_domain": ""},
            "inv-name",
            "inv-name",
        ),
        (
            {"ansible_nodename": "host.example.com"},
            "inv-name",
            "host.example.com",
        ),
        ({"ansible_nodename": "host"}, "inv-name", "inv-name"),
        ({}, "inv-name", "inv-name"),
        (
            {
                "ansible_fqdn": "web1.example.com",
                "ansible_hostname": "web1",
                "ansible_domain": "different.example.com",
            },
            "inv-name",
            "web1.example.com",
        ),
    ]

    def test_all_cases_agree(self) -> None:
        for facts, inv, expected in self.CASES:
            assert resolve_fqdn(facts, inv) == expected, (
                f"drift on input ({facts}, {inv}): "
                f"got {resolve_fqdn(facts, inv)!r}, expected {expected!r}"
            )

    PREFIX_CASES = [
        # (input, expected_output)
        (
            {"fqdn": "x", "default_ipv4": {"address": "10.0.0.1"}},
            {"ansible_fqdn": "x", "ansible_default_ipv4": {"address": "10.0.0.1"}},
        ),
        (
            {"ansible_fqdn": "x"},
            {"ansible_fqdn": "x"},
        ),
        (
            {"fqdn": "x", "ansible_distribution": "Debian"},
            {"ansible_fqdn": "x", "ansible_distribution": "Debian"},
        ),
        ({}, {}),
    ]

    def test_ensure_prefix_cases_agree(self) -> None:
        for raw, expected in self.PREFIX_CASES:
            assert ensure_ansible_prefix(raw) == expected, (
                f"drift on input {raw}: "
                f"got {ensure_ansible_prefix(raw)!r}, expected {expected!r}"
            )
