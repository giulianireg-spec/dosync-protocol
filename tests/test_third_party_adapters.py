"""Adapters from packages the operator installed (2026-07-27).

The third of the three ways a technology reaches a deployment, and the one that
lets someone other than this project answer for a device. DESIGN-PRINCIPLES rules
out fetching adapter code from a remote source; an entry point differs in the two
ways that matter — someone chose to install it, and someone's name is on it.
"""
from pathlib import Path

import pytest

from dosync.plugins import ENTRY_POINT_GROUP, discover_third_party_adapters

REPO = Path(__file__).resolve().parent.parent


class _NotAnAdapter:
    pass


class _Explodes:
    def __init__(self, hub=None):
        raise RuntimeError("vendor bug")


class _Fine:
    """Duck-typed on purpose — must still be rejected for not inheriting."""
    adapter_name = "fine"


def _fake_entry_point(name, obj, dist="pkg-x", fail_import=False):
    class EP:
        def __init__(self):
            self.name = name
            self.dist = type("D", (), {"name": dist})()

        def load(self):
            if fail_import:
                raise ImportError("no module named whatever")
            return obj
    return EP()


def _run_with(monkeypatch, eps):
    monkeypatch.setattr("dosync.plugins.entry_points", lambda **k: eps, raising=False)
    import dosync.plugins as plugins
    monkeypatch.setattr(plugins, "discover_third_party_adapters",
                        plugins.discover_third_party_adapters)
    return eps


def test_the_entry_point_group_is_stable():
    """Third parties publish against this string; changing it silently orphans
    every plugin in existence."""
    assert ENTRY_POINT_GROUP == "dosync.adapters"


def test_a_real_adapter_is_loaded_and_marked_third_party(monkeypatch):
    """And the mark is applied by the LOADER. The test adapter declares itself
    `ecosystem` — a plugin claiming to be first-party code of this project —
    and must not be believed."""
    from dosync.adapters import DoSyncAdapter
    from dosync.models import ActionResult

    class Vendor(DoSyncAdapter):
        adapter_name = "vendor"
        adapter_kind = "ecosystem"          # the lie

        def __init__(self, hub=None):
            self.hub = hub

        async def execute(self, action, urgency):
            return ActionResult(device_id=action.device_id,
                                action=action.action, success=True)

    import dosync.plugins as plugins
    monkeypatch.setattr(plugins, "entry_points",
                        lambda **k: [_fake_entry_point("vendor", Vendor)],
                        raising=False)

    loaded = plugins.discover_third_party_adapters(hub="HUB")
    assert len(loaded) == 1
    name, adapter, origin = loaded[0]
    assert name == "vendor" and origin == "pkg-x"
    assert adapter.adapter_kind == "third_party", \
        "where code came from is not the code's to assert"
    assert adapter.hub == "HUB", "the hub is passed when the constructor wants it"


def test_a_plugin_that_is_not_an_adapter_is_refused(monkeypatch):
    import dosync.plugins as plugins
    monkeypatch.setattr(plugins, "entry_points",
                        lambda **k: [_fake_entry_point("bad", _NotAnAdapter)],
                        raising=False)
    assert plugins.discover_third_party_adapters() == []


def test_duck_typing_is_not_enough(monkeypatch):
    """The same lesson as NotificationAdapter: matching a couple of attributes
    works until the base class grows one you silently lack."""
    import dosync.plugins as plugins
    monkeypatch.setattr(plugins, "entry_points",
                        lambda **k: [_fake_entry_point("fine", _Fine)],
                        raising=False)
    assert plugins.discover_third_party_adapters() == []


def test_a_broken_plugin_does_not_stop_the_hub(monkeypatch, caplog):
    """One vendor's bad release must not take a building offline."""
    import logging

    from dosync.adapters import DoSyncAdapter
    from dosync.models import ActionResult

    class Good(DoSyncAdapter):
        adapter_name = "good"

        def __init__(self, hub=None):
            pass

        async def execute(self, action, urgency):
            return ActionResult(device_id="d", action="a", success=True)

    import dosync.plugins as plugins
    monkeypatch.setattr(plugins, "entry_points", lambda **k: [
        _fake_entry_point("broken-import", Good, fail_import=True),
        _fake_entry_point("explodes", _Explodes),
        _fake_entry_point("good", Good),
    ], raising=False)

    with caplog.at_level(logging.ERROR):
        loaded = plugins.discover_third_party_adapters()

    assert [n for n, _, _ in loaded] == ["good"], \
        "the working plugin still loads"
    assert sum("skipped" in str(r.msg) for r in caplog.records) == 2


def test_loading_is_announced_loudly(monkeypatch, caplog):
    """This is code running inside the hub with the hub's permissions. It is
    logged at WARNING, not INFO, because an operator scanning for surprises
    should find it."""
    import logging

    from dosync.adapters import DoSyncAdapter
    from dosync.models import ActionResult

    class Vendor(DoSyncAdapter):
        adapter_name = "v"

        def __init__(self, hub=None):
            pass

        async def execute(self, action, urgency):
            return ActionResult(device_id="d", action="a", success=True)

    import dosync.plugins as plugins
    monkeypatch.setattr(plugins, "entry_points",
                        lambda **k: [_fake_entry_point("v", Vendor)], raising=False)

    with caplog.at_level(logging.WARNING):
        plugins.discover_third_party_adapters()
    assert any("hub's permissions" in str(r.msg) for r in caplog.records)


def test_the_server_records_third_party_adapters_in_the_chain():
    """'What code was running when this happened' is a question an incident
    review asks."""
    import inspect

    import dosync.server as srv
    src = inspect.getsource(srv)
    assert "third_party_adapter_loaded" in src
    assert "hub.audit_log.append" in src


def test_publishing_one_is_documented():
    readme = (REPO / "README.md").read_text()
    principles = (REPO / "docs" / "DESIGN-PRINCIPLES.md").read_text()
    assert "dosync.adapters" in readme, \
        "a vendor must be able to find the entry point group"
    assert "entry point" in principles.lower()
