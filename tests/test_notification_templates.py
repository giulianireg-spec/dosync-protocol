"""Operator-supplied notification templates.

The message bodies used to be five hand-written templates about a home, one of
them ending in a single country's emergency number. They are now the
deployment's own words, supplied through DOSYNC_NOTIFICATION_TEMPLATES.

This file exists because the first implementation of that shipped with a defect
that only the happy path would have caught: the protocol's fields were passed
as keywords alongside `**context`, so any context carrying "location" — which
is most emergencies — raised TypeError, and the exception escaped
_build_message and took the whole notification with it. The fallback for broken
templates had been written and tested; the case where a template WORKS had not.
"""
import json

import pytest

from dosync.adapters.notifications import NotificationAdapter

TEMPLATES = {
    "ensure_safety": "EMERGENCY {location}\nProtocol activated.",
    "alert_anomaly": "ALERT\nAbnormal condition at {location}.",
    "notify": "MSG\n{message}",
    "broken": "{nonexistent_field}",
}


@pytest.fixture
def adapter():
    return NotificationAdapter(templates=dict(TEMPLATES))


def test_a_template_renders_with_a_location_in_context(adapter):
    """The regression: context and protocol fields must not collide."""
    body = adapter._build_message("ensure_safety", "emergency",
                                  {"location": "sector-7g"})
    assert body == "EMERGENCY sector-7g\nProtocol activated."


def test_a_template_renders_without_a_location(adapter):
    body = adapter._build_message("ensure_safety", "emergency", {})
    assert "EMERGENCY" in body and "Protocol activated." in body


def test_context_fields_reach_the_template(adapter):
    assert adapter._build_message("notify", "info", {"message": "hello"}) \
        == "MSG\nhello"


def test_a_broken_template_falls_back_instead_of_raising(adapter):
    """A template must never be able to silence an alert."""
    body = adapter._build_message("broken", "emergency", {"location": "or-3"})
    assert "broken" in body and "DOSYNC" in body


def test_a_missing_context_field_falls_back(adapter):
    body = adapter._build_message("notify", "info", {})
    assert body.startswith("DOSYNC")


def test_without_templates_the_default_is_factual_and_domain_neutral():
    """No home, no country, no advice — only what the hub knows."""
    body = NotificationAdapter(templates={})._build_message(
        "ensure_safety", "emergency", {"location": "ward-2"})
    assert body == "DOSYNC EMERGENCY at ward-2\nIntent: ensure_safety"
    for word in ("home", "hogar", "107", "call", "casa"):
        assert word.lower() not in body.lower()


def test_templates_load_from_the_declared_file(tmp_path, monkeypatch):
    path = tmp_path / "templates.json"
    path.write_text(json.dumps({"notify": "X {message}"}), encoding="utf-8")
    monkeypatch.setenv("DOSYNC_NOTIFICATION_TEMPLATES", str(path))
    assert NotificationAdapter()._build_message("notify", "info",
                                                {"message": "y"}) == "X y"


def test_an_unreadable_template_file_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("DOSYNC_NOTIFICATION_TEMPLATES",
                       str(tmp_path / "does-not-exist.json"))
    body = NotificationAdapter()._build_message("notify", "info", {"message": "y"})
    assert body.startswith("DOSYNC")
