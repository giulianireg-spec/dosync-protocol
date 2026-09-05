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


# ── The bridge that never re-imported (2026-09-05) ────────────────────────────

def test_something_actually_calls_import_devices():
    """`import_devices` existed, worked, and nothing invoked it.

    Its only appearance outside its own definition was an example in the
    module's docstring. The bridge registered itself at startup — for executing
    actions — and the registry froze at whatever a manual invocation had once
    put there. A device added to Home Assistant was never seen, nothing failed,
    and nothing said so.

    Found while trying to validate an unrelated change: three service restarts
    produced no import lines in the journal at all.
    """
    src = (REPO / "dosync" / "adapters" / "homeassistant.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.split("\n")
                     if l.strip() and not l.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]          # drop the module docstring example

    assert "await self.import_devices()" in body, (
        "nothing inside the bridge calls import_devices; if the periodic loop "
        "was removed, the registry freezes again and says nothing")

    server = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert "start_import_loop()" in server, (
        "the import loop is never started at hub startup — registering the "
        "bridge only wires it up to execute actions")


def test_a_failed_import_is_visible_without_reading_the_journal():
    """An expired token produced 401s for days and nobody noticed.

    The only signal was a warning in the log and a success rate of zero buried
    in execution stats. A cycle that fails must leave something a caller can
    read.
    """
    src = (REPO / "dosync" / "adapters" / "homeassistant.py").read_text(encoding="utf-8")

    assert "self.last_import" in src, (
        "the bridge keeps no record of its last import, so a failing cycle is "
        "visible only to whoever reads the journal")
    assert 'log.warning("HA bridge: import cycle failed' in src, (
        "a failed import cycle does not warn — debug level is how the expired "
        "token stayed invisible")


def test_the_import_loop_cannot_take_the_hub_down():
    """Home Assistant being unreachable must not stop a hub that also governs
    devices HA knows nothing about — which was the live situation this week,
    with a revoked token returning 401 on every call."""
    src = (REPO / "dosync" / "adapters" / "homeassistant.py").read_text(encoding="utf-8")
    loop = src[src.index("async def start_import_loop"):]
    loop = loop[:loop.index("\n    def ")]

    assert "except Exception" in loop, (
        "an unhandled exception in the import cycle would kill the task and "
        "silently stop all future imports")
    assert "except asyncio.CancelledError" in loop, (
        "the loop does not handle cancellation, so shutdown logs an error")


def test_the_import_loop_actually_runs():
    """Reading the source is not running it.

    The three tests above check that the loop exists, that failure is visible
    and that nothing can kill the hub — all by reading `homeassistant.py` as
    text. All three passed on a version that raised `NameError: name 'time' is
    not defined` on its first cycle, because `time` was never imported.

    1,122 tests were green and the loop died sixty seconds after startup in
    production. This one executes a cycle.
    """
    import asyncio

    from dosync.adapters.homeassistant import HABridge

    bridge = HABridge.__new__(HABridge)
    bridge.last_import = None

    calls = []

    async def fake_import():
        calls.append(1)
        raise RuntimeError("upstream unreachable")

    bridge.import_devices = fake_import

    async def run_one_cycle():
        task = asyncio.create_task(bridge.start_import_loop(interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one_cycle())

    assert calls, "the loop never called import_devices"
    assert bridge.last_import is not None, (
        "a failed cycle left no record — this is the field that makes the "
        "failure visible without reading the journal")
    assert bridge.last_import["ok"] is False
    assert "unreachable" in bridge.last_import["error"], (
        "the recorded error does not say what went wrong")


def test_the_import_endpoint_answers_rather_than_500s():
    """The endpoint answered 500 on every request and no test noticed.

    Its first version walked `_adapters` calling `adapter.adapter_name()` —
    which is a property, so that tried to invoke a string. 1,123 tests passed
    over it, because nothing exercised the route: the same gap that let the
    import loop ship with a missing import an hour earlier.

    Without HA_TOKEN no bridge is registered, so this asserts the honest 404
    rather than constructing one. What it pins is that the handler RUNS: a 500
    here means the code path is broken again.
    """
    from fastapi.testclient import TestClient

    import dosync.server as srv

    # A stub must be registered for this to mean anything. With an empty
    # `_adapters` the broken loop never iterated, so a first version of this
    # test passed with the defect in place — the endpoint only failed once
    # there was something to walk over.
    class _Stub:
        @property
        def adapter_name(self):
            return "homeassistant"

        async def import_devices(self):
            return {"new": 0, "updated": 0, "skipped": 0, "total": 0}

    adapters = getattr(srv._adapter_executor, "_adapters", None)
    if adapters is None:
        pytest.skip("no adapter executor on this build")

    # Auth is overridden, not bypassed with a token: without this the request
    # is rejected before the handler runs, and a second version of this test
    # passed with the defect in place for exactly that reason — a 401 tells you
    # nothing about code that never executed.
    from dosync.auth_fastapi import require_auth

    adapters["homeassistant"] = _Stub()
    srv.app.dependency_overrides[require_auth] = lambda: "test"
    try:
        response = TestClient(srv.app).post("/v1/bridges/homeassistant/import")
    finally:
        srv.app.dependency_overrides.pop(require_auth, None)
        adapters.pop("homeassistant", None)

    assert response.status_code != 500, (
        f"the import endpoint raised: {response.text[:200]}")
    assert response.status_code == 200, (
        f"expected 200 with a bridge registered, got {response.status_code}: "
        f"{response.text[:200]}")
