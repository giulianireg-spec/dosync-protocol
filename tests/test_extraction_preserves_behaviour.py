"""Phase 2 extractions must change addresses, not behaviour.

`hub.py` held five responsibilities in 3,710 lines: the capability registry,
four resolvers, the audit log, a timed executor and the device-health monitor.
None could be changed without risk to the other four, which is why the redesign
of the resolver has to wait for them to be separated.

An extraction is only safe if callers cannot tell it happened. These tests pin
that property for each piece as it moves.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_the_audit_log_is_one_class_at_two_addresses():
    """Re-exported, not copied.

    Callers — `manage.py`, `audit_backup.py`, several test modules — import
    `AuditLog` from `dosync.hub`. If the extraction had left a copy behind,
    two chains would exist and only one would be persisted.
    """
    from dosync.audit import AuditLog as extracted
    from dosync.hub import AuditLog as reexported

    assert reexported is extracted, (
        "dosync.hub.AuditLog and dosync.audit.AuditLog are different objects — "
        "the extraction copied the class instead of re-exporting it")


def test_audit_records_still_reach_the_hub_logger():
    """The extracted module logs to `dosync.hub`, not `dosync.audit`.

    An operator filtering on the old logger name would otherwise stop seeing
    audit warnings the day this module moved — a behaviour change hiding inside
    a move that promised none.
    """
    import dosync.audit as audit

    assert audit.log.name == "dosync.hub", (
        f"audit records now go to the logger {audit.log.name!r}; anything "
        "filtering on 'dosync.hub' would silently lose them")


def test_the_audit_log_is_defined_once():
    """`is` proves the two names point at one object today.

    It would not catch a stale second definition left behind further down in
    hub.py and shadowed by the import — which would sit there looking correct
    until someone reordered the file.
    """
    hub_src = (REPO / "dosync" / "hub.py").read_text(encoding="utf-8")
    assert "class AuditLog" not in hub_src, (
        "hub.py still defines AuditLog; the extraction left a copy behind")


def test_hub_is_smaller_than_it_was():
    """A guard against the extraction being undone by a later merge.

    hub.py was 3,710 lines when Phase 2 began. This does not assert a target —
    it asserts that the separation did not quietly reverse.
    """
    # The threshold drops with each Phase 2 extraction. It is a ratchet for the
    # duration of the phase, not a permanent size limit — once the separations
    # are done this test should be retired rather than raised, or it will start
    # failing for legitimate additions and blaming the wrong cause.
    lines = (REPO / "dosync" / "hub.py").read_text(encoding="utf-8").count("\n")
    assert lines < 3600, (
        f"hub.py is back to {lines} lines: something moved back in, or an "
        "extraction was reverted")
