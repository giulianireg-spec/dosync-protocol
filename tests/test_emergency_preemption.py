"""
Deterministic concurrency tests for emergency preemption (DeviceArbiter).

These tests do NOT use sleeps or timing — a race tested with `sleep + assert` is
flaky and gives false greens. Instead a BarrierExecutor lets each test pin the exact
interleaving: an action can be held "in flight" (mid-send, holding the per-device
lock) at a known point, a second action fired, and the gate released in the order
under test. Time is injected (now_fn) so the claim-window expiry is deterministic too.

Contract under test (spec/CONSISTENCY-MODEL.md §3, revised):
  An emergency-urgency write is the DEVICE-FINAL write within a bounded window;
  lower-urgency writes on a claimed device are dropped (success=False, superseded)
  and never reach the device — without aborting an in-flight action mid-send.

Run:  python3 -m pytest tests/test_emergency_preemption.py -v
"""

from __future__ import annotations

import asyncio

from dosync.models import ActionResult, DeviceAction, Urgency
from dosync.executor import DeviceExecutor
from dosync.device_arbiter import DeviceArbiter


# ── helpers ──────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def act(device_id: str, action: str) -> DeviceAction:
    return DeviceAction(device_id=device_id, action=action, params={})


class BarrierExecutor(DeviceExecutor):
    """Inner executor that records what actually reached the device and can block a
    call mid-send at a per-device gate, so tests control the interleaving exactly."""

    def __init__(self):
        self.applied: list[tuple[str, str]] = []      # (device, action) actually applied, in order
        self.final_state: dict[str, str] = {}         # device -> last action applied
        self.call_log: list[tuple[str, str]] = []      # every execute() that entered the inner layer
        self._gates: dict[str, asyncio.Event] = {}
        self._reached: dict[str, asyncio.Event] = {}

    def gate(self, device_id: str) -> asyncio.Event:
        """Make the next inner execute() on this device block until the event is set.
        Returns the event the test releases."""
        self._gates[device_id] = asyncio.Event()
        self._reached[device_id] = asyncio.Event()
        return self._gates[device_id]

    async def reached(self, device_id: str) -> None:
        """Await until an inner execute() on this device has reached its gate (i.e. it
        is mid-send and holding the per-device lock)."""
        ev = self._reached.get(device_id)
        if ev is not None:
            await ev.wait()

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        self.call_log.append((action.device_id, action.action))
        gate = self._gates.get(action.device_id)
        if gate is not None:
            reached = self._reached.get(action.device_id)
            if reached is not None:
                reached.set()
            await gate.wait()
        self.applied.append((action.device_id, action.action))
        self.final_state[action.device_id] = action.action
        return ActionResult(device_id=action.device_id, action=action.action,
                            success=True, response={"applied": action.action})


def _arbiter(inner, **kw):
    # Default: claim threshold = emergency. A HELD claim (never released) stays
    # active until max_hold, so the interleaving tests below drop lower-urgency
    # actions without needing to call release_claim().
    return DeviceArbiter(inner, **kw)


# ── 1. emergency after routine fully completes ────────────────────────────────

def test_emergency_after_routine_is_device_final():
    async def scenario():
        bar = BarrierExecutor()
        arb = _arbiter(bar)
        r1 = await arb.execute(act("light", "dim"), Urgency.INFO)          # routine first
        r2 = await arb.execute(act("light", "full"), Urgency.EMERGENCY)    # emergency after
        r3 = await arb.execute(act("light", "dim_again"), Urgency.INFO)    # routine during window
        return bar, r1, r2, r3
    bar, r1, r2, r3 = run(scenario())
    assert r1.success is True
    assert r2.success is True
    assert r3.success is False and r3.response["superseded"] is True       # dropped
    assert bar.final_state["light"] == "full"                              # emergency is final
    assert ("light", "dim_again") not in bar.applied                       # never reached device


# ── 2. emergency before routine (routine arrives during the claim window) ─────

def test_emergency_before_routine_blocks_routine():
    async def scenario():
        bar = BarrierExecutor()
        arb = _arbiter(bar)
        r_emr = await arb.execute(act("light", "full"), Urgency.EMERGENCY)
        r_rt = await arb.execute(act("light", "dim"), Urgency.INFO)        # should be superseded
        return bar, r_emr, r_rt
    bar, r_emr, r_rt = run(scenario())
    assert r_emr.success is True
    assert r_rt.success is False and r_rt.response["superseded"] is True
    assert bar.final_state["light"] == "full"
    assert bar.applied == [("light", "full")]                             # routine never applied


# ── 3. emergency DURING an in-flight routine (the hard interleaving) ──────────

def test_emergency_during_inflight_routine():
    async def scenario():
        bar = BarrierExecutor()
        arb = _arbiter(bar)
        gate = bar.gate("light")

        # routine acquires the per-device lock and blocks mid-send
        t_routine = asyncio.create_task(arb.execute(act("light", "dim"), Urgency.INFO))
        await bar.reached("light")            # routine is now in flight, holding the lock

        # emergency arrives: sets the claim, then waits for the lock
        t_emr = asyncio.create_task(arb.execute(act("light", "full"), Urgency.EMERGENCY))
        await asyncio.sleep(0)                # let the emergency set its claim + queue on the lock

        # a second routine arrives during the claim → must self-drop before the lock
        r_late = await arb.execute(act("light", "dim_late"), Urgency.INFO)

        gate.set()                            # release the in-flight routine
        r_routine = await t_routine
        r_emr = await t_emr
        return bar, r_routine, r_emr, r_late

    bar, r_routine, r_emr, r_late = run(scenario())
    assert r_routine.success is True          # the in-flight action was NOT aborted mid-send
    assert r_emr.success is True
    assert r_late.success is False and r_late.response["superseded"] is True
    # device sees: routine's in-flight write, then the emergency overwrites it (final)
    assert bar.applied == [("light", "dim"), ("light", "full")]
    assert bar.final_state["light"] == "full"
    assert ("light", "dim_late") not in bar.call_log   # dropped before reaching the device


# ── 4. claim is HELD until the emergency intent is released ───────────────────

def test_claim_held_until_released():
    """While the emergency intent is active (not released), lower-urgency actions on
    the device are dropped — regardless of how much wall-clock time passes."""
    async def scenario():
        clock = [1000.0]
        bar = BarrierExecutor()
        arb = DeviceArbiter(bar, grace=5.0, max_hold=1000.0, now_fn=lambda: clock[0])
        await arb.execute(act("light", "full"), Urgency.EMERGENCY)     # claim HELD
        clock[0] = 1050.0                                              # 50s later, still not released
        r_rt = await arb.execute(act("light", "dim"), Urgency.INFO)
        return bar, r_rt
    bar, r_rt = run(scenario())
    assert r_rt.success is False and r_rt.response["superseded"] is True   # still owned
    assert bar.final_state["light"] == "full"


# ── 5. after release, the claim lingers only for `grace`, then frees the device ─

def test_grace_expires_after_release():
    async def scenario():
        clock = [1000.0]
        bar = BarrierExecutor()
        arb = DeviceArbiter(bar, grace=10.0, max_hold=1000.0, now_fn=lambda: clock[0])
        await arb.execute(act("light", "full"), Urgency.EMERGENCY)     # claim HELD
        arb.release_claim(["light"])                                   # intent completed → grace starts (until 1010)
        r_mid = await arb.execute(act("light", "dim"), Urgency.INFO)   # clock=1000, within grace
        clock[0] = 1011.0                                              # past grace
        r_after = await arb.execute(act("light", "scene"), Urgency.INFO)
        return bar, r_mid, r_after
    bar, r_mid, r_after = run(scenario())
    assert r_mid.success is False and r_mid.response["superseded"] is True  # grace still protects
    assert r_after.success is True                                          # freed after grace
    assert bar.final_state["light"] == "scene"


# ── 6. safety cap — a claim never locks a device forever if release is missed ──

def test_max_hold_safety_cap():
    """If the hub never calls release_claim (wiring bug/crash), the claim still
    expires after max_hold so the device is not locked forever."""
    async def scenario():
        clock = [1000.0]
        bar = BarrierExecutor()
        arb = DeviceArbiter(bar, grace=5.0, max_hold=50.0, now_fn=lambda: clock[0])
        await arb.execute(act("light", "full"), Urgency.EMERGENCY)     # HELD, no release
        clock[0] = 1040.0
        r_within = await arb.execute(act("light", "dim"), Urgency.INFO)    # within max_hold
        clock[0] = 1051.0
        r_after = await arb.execute(act("light", "scene"), Urgency.INFO)   # past max_hold
        return bar, r_within, r_after
    bar, r_within, r_after = run(scenario())
    assert r_within.success is False and r_within.response["superseded"] is True
    assert r_after.success is True                     # safety cap freed the device
    assert bar.final_state["light"] == "scene"


# ── 7. clear_claims frees everything immediately (deterministic reset) ─────────

def test_clear_claims_frees_device():
    async def scenario():
        bar = BarrierExecutor()
        arb = _arbiter(bar)
        await arb.execute(act("light", "full"), Urgency.EMERGENCY)
        assert arb.active_claims() == {"light": "emergency"}
        arb.clear_claims()
        r_rt = await arb.execute(act("light", "dim"), Urgency.INFO)
        return arb, r_rt
    arb, r_rt = run(scenario())
    assert r_rt.success is True
    assert arb.active_claims() == {}


# ── 5. different devices run in parallel (claim is per-device) ────────────────

def test_claim_is_per_device():
    async def scenario():
        bar = BarrierExecutor()
        arb = _arbiter(bar)
        await arb.execute(act("light_a", "full"), Urgency.EMERGENCY)         # claims light_a only
        r_b = await arb.execute(act("light_b", "dim"), Urgency.INFO)         # different device
        return bar, r_b
    bar, r_b = run(scenario())
    assert r_b.success is True
    assert bar.final_state["light_b"] == "dim"        # unaffected by light_a's claim


# ── 6. no claim, no supersede — ordinary same-device actions both apply ───────

def test_no_claim_both_routines_apply():
    async def scenario():
        bar = BarrierExecutor()
        arb = _arbiter(bar)
        r1 = await arb.execute(act("light", "scene1"), Urgency.INFO)
        r2 = await arb.execute(act("light", "scene2"), Urgency.INFO)
        return bar, r1, r2
    bar, r1, r2 = run(scenario())
    assert r1.success is True and r2.success is True   # neither is dropped
    assert bar.final_state["light"] == "scene2"        # plain last-write, serialized


# ── 7. supersede is reported to the audit hook ────────────────────────────────

def test_audit_hook_records_supersede():
    audit: list[dict] = []

    async def scenario():
        bar = BarrierExecutor()
        arb = _arbiter(bar, audit_hook=audit.append)
        await arb.execute(act("light", "full"), Urgency.EMERGENCY)
        await arb.execute(act("light", "dim"), Urgency.INFO)   # superseded → audited
        return bar
    run(scenario())
    assert len(audit) == 1
    entry = audit[0]
    assert entry["type"] == "action_superseded_by_priority"
    assert entry["device_id"] == "light"
    assert entry["action"] == "dim"
    assert entry["claimed_by_urgency"] == "emergency"


# ── 8. emergency never waits for more than the single in-flight action ────────

def test_emergency_does_not_wait_for_queued_lower_priority():
    """Two lower-priority actions are queued behind an in-flight one; when the
    emergency arrives it must not wait for the queue to drain — the queued lower
    actions self-drop, so the emergency only ever waits for the one mid-send."""
    async def scenario():
        bar = BarrierExecutor()
        arb = _arbiter(bar)
        gate = bar.gate("light")
        t0 = asyncio.create_task(arb.execute(act("light", "dim0"), Urgency.INFO))
        await bar.reached("light")                      # dim0 in flight, holds lock
        # queue two more routines behind the lock
        t1 = asyncio.create_task(arb.execute(act("light", "dim1"), Urgency.INFO))
        t2 = asyncio.create_task(arb.execute(act("light", "dim2"), Urgency.INFO))
        await asyncio.sleep(0)
        # emergency arrives
        t_emr = asyncio.create_task(arb.execute(act("light", "full"), Urgency.EMERGENCY))
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(t0, t1, t2, t_emr)
        return bar, results
    bar, (r0, r1, r2, r_emr) = run(scenario())
    assert r0.success is True                           # the one in flight completed
    assert r_emr.success is True
    # the two queued lower-priority actions were superseded by the emergency claim
    assert r1.success is False and r2.success is False
    assert bar.final_state["light"] == "full"
    # only dim0 (in flight) and full (emergency) ever reached the device
    assert bar.applied == [("light", "dim0"), ("light", "full")]


def test_lower_urgency_release_does_not_free_emergency_claim():
    """If a routine sharing a device with an emergency completes FIRST, its
    release_claim(rank=0) must not start the grace on the emergency's claim (rank=3)."""
    async def scenario():
        clock = [1000.0]
        bar = BarrierExecutor()
        arb = DeviceArbiter(bar, grace=5.0, max_hold=1000.0, now_fn=lambda: clock[0])
        await arb.execute(act("light", "full"), Urgency.EMERGENCY)   # emergency claim (rank 3, held)
        arb.release_claim(["light"], rank=0)                         # routine completes first → must NOT release
        clock[0] = 1100.0                                            # long after
        r = await arb.execute(act("light", "dim"), Urgency.INFO)
        return r
    r = run(scenario())
    assert r.success is False and r.response["superseded"] is True   # emergency claim still held


if __name__ == "__main__":
    # Standalone runner (no pytest required), mirrors the repo's asyncio.run style.
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
