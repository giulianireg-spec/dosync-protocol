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


def test_execution_timing_and_health_are_one_class_at_two_addresses():
    """`adapters/__init__.py` and three test modules import these from
    `dosync.hub`. A copy rather than a re-export would give the adapters one
    health tracker and the hub another, and neither would see the other's
    failures."""
    from dosync.execution import DeviceHealth as h_extracted
    from dosync.execution import _TimedExecutor as t_extracted
    from dosync.hub import DeviceHealth as h_reexported
    from dosync.hub import _TimedExecutor as t_reexported

    assert h_reexported is h_extracted, "DeviceHealth was copied, not re-exported"
    assert t_reexported is t_extracted, "_TimedExecutor was copied, not re-exported"


def test_execution_records_still_reach_the_hub_logger():
    """Same reason as the audit module: an operator filtering on `dosync.hub`
    should not lose these the day the file moved."""
    import dosync.execution as execution

    assert execution.log.name == "dosync.hub", (
        f"execution records now go to {execution.log.name!r}")


def test_the_extraction_took_the_classes_and_left_the_functions():
    """The first attempt at this move swept up two module-level functions that
    sat between the classes — `checkpoint_export_mode` and
    `_assurance_is_regulated` — and fourteen tests failed because callers
    import them from `dosync.hub`.

    Line ranges are a blunt instrument for extraction; what belongs together
    conceptually is not always contiguous.
    """
    import dosync.hub as hub

    assert hasattr(hub, "checkpoint_export_mode"), \
        "checkpoint_export_mode left hub.py; callers import it from there"
    assert hasattr(hub, "_assurance_is_regulated"), \
        "_assurance_is_regulated left hub.py"


def test_checkpoint_state_is_not_duplicated():
    """The hub exposes the keeper's bookkeeping as properties, not copies.

    Tests and `server.py` read `hub._checkpoint_export_state` and friends. If
    the extraction had copied those attributes onto the hub as well, there
    would be two values and only one of them would be updated — the kind of
    drift that shows up as a checkpoint the hub believes it wrote.
    """
    from dosync.hub import DoSyncHub

    hub = DoSyncHub(db_path=":memory:")
    hub._checkpoints._checkpoint_export_state = "sentinel"
    assert hub._checkpoint_export_state == "sentinel", (
        "hub._checkpoint_export_state does not follow the keeper — it is a "
        "copy, and the two will drift")


def test_the_default_checkpoint_interval_is_visible_at_the_entry_point():
    """An implementer looking for the default looks at the method they call.

    Delegating the body to CheckpointKeeper hid the number one level down. It
    is repeated in the docstring so it stays where someone would look — which
    a pre-existing test asserts by reading the source.
    """
    import inspect

    from dosync.hub import DoSyncHub

    src = inspect.getsource(DoSyncHub.start_checkpoint_scheduler)
    assert '"86400"' in src, (
        "the default interval is no longer visible in the method an "
        "implementer calls")


def test_the_resolvers_are_one_set_of_classes_at_two_addresses():
    """`server.py` swaps in an ExternalResolver at startup and several tests
    replace `resolve` outright. A copy rather than a re-export would leave the
    hub holding one class and the caller constructing another."""
    from dosync import hub, resolvers

    for name in ("BaseResolver", "ExternalResolver", "CapabilityMatchingResolver",
                 "StateAwareResolver", "ScoreBreakdown"):
        assert getattr(hub, name) is getattr(resolvers, name), (
            f"{name} was copied into hub.py rather than re-exported")


def test_the_resolver_can_still_be_replaced_at_runtime():
    """The substitution `server.py` performs — `hub.resolver = ExternalResolver(...)`
    — must survive the move, because the deployment that uses an external
    resolution service depends on it."""
    from dosync.hub import DoSyncHub
    from dosync.resolvers import ExternalResolver

    hub = DoSyncHub(db_path=":memory:")
    hub.resolver = ExternalResolver(hub.registry, "http://example.invalid")
    assert isinstance(hub.resolver, ExternalResolver)


def test_the_resolvers_do_not_import_the_hub_at_module_level():
    """hub.py imports resolvers.py. A module-level import back would close the
    cycle, and the one function that needs `is_quarantined` imports it inside
    the call instead.

    A first attempt at that indirection replaced the call inside the wrapper
    with the wrapper's own name — the function called itself, and fifty-one
    tests died of recursion. Blind text replacement does not respect scope.
    """
    import ast

    src = (REPO / "dosync" / "resolvers.py").read_text(encoding="utf-8")

    # Parsed rather than string-sliced. A first version of this test cut the
    # file at the first "class " and searched the remainder, which put the
    # lazy import — a function defined above the classes — inside the region it
    # was checking. It failed on correct code.
    tree = ast.parse(src)
    module_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    for node in module_level:
        mod = getattr(node, "module", "") or ""
        assert "hub" not in mod, (
            f"resolvers.py imports {mod!r} at module level; hub.py imports "
            "resolvers.py, so this closes an import cycle")

    # The lazy-import wrapper this used to check is gone: `is_quarantined` now
    # lives in resolvers.py itself, which is where its callers were. The debt
    # was declared when the resolvers moved and paid separately.
    assert "def is_quarantined" in src, (
        "is_quarantined is no longer defined in resolvers.py — if it moved "
        "back to hub.py, the lazy import and its cycle come back with it")


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
    assert lines < 2050, (
        f"hub.py is back to {lines} lines: something moved back in, or an "
        "extraction was reverted")
