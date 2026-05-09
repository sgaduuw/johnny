"""Tests for johnny.persistence._fqdn.resolve_fqdn.

The function is a verbatim port of the same-named helper in the
johnny-callback plugin (see plugins/callback/callback.py:99 in that
repo). If this file's expected outputs ever diverge from the
plugin's tests, one side has drifted: bump the matching side or
the convergence guarantee breaks.
"""

from __future__ import annotations

from johnny.persistence._fqdn import resolve_fqdn


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

    The callback plugin owns the canonical _resolve_fqdn. This johnny
    copy must agree on every input the plugin tests. If a row here
    fails, the two implementations have drifted; one of them is
    behind. Update the lagging side, do not relax the assertion.

    Plugin source pinned at:
      sgaduuw/johnny-callback @ 4d46bee :: plugins/callback/callback.py:99
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
