"""Execution timing and device health.

Extracted from `hub.py` on 2 September 2026, the second of the Phase 2 moves.
`_TimedExecutor` wraps an execution to record how long it took; `DeviceHealth`
tracks per-device success and failure so the hub can report which devices are
answering and which are not.

Neither belongs beside a capability registry and four resolvers, which is where
they lived. Both are re-exported from `dosync.hub`: `adapters/__init__.py` and
three test modules import them from there, and an extraction that forces its
callers to move is a rewrite with better manners.

The two are here together on purpose, even though device health is not really
execution: they are coupled by data. `_TimedExecutor` produces the result that
`DeviceHealth` consumes, and splitting them now would turn an internal call into
a dependency between two new modules for no gain.

The code is unchanged. Only its address is.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque

# "dosync.hub" and not "dosync.execution", deliberately: these records went to
# that logger before the move, and an operator filtering on it would otherwise
# stop seeing them the day the file changed.
log = logging.getLogger("dosync.hub")


class _TimedExecutor:
    """Transparent executor wrapper that records per-action execution latency.

    Delegates everything to the wrapped executor; only execute() is timed. Any
    attribute the caller expects (release_claim, etc.) passes through. This keeps
    latency instrumentation out of every execution path in the hub.
    """

    _dosync_timed = True

    def __init__(self, inner, hub=None):
        self._inner = inner
        self._hub = hub

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def execute(self, action, urgency):
        _t0 = time.perf_counter()
        result = await self._inner.execute(action, urgency)
        success = bool(getattr(result, "success", False))
        try:
            if _M is not None:
                _M.action_execution_seconds.observe(
                    time.perf_counter() - _t0,
                    {"result": "success" if success else "failed"},
                )
        except Exception:
            pass
        # Device health: every real action is a health signal, recorded at this
        # single chokepoint (all execution paths funnel through here) rather than
        # scattered across parallel/batch/retry paths. Two complementary sinks,
        # both previously built-but-unwired (no code populated them, so the
        # /v1/health endpoints returned empty in production):
        #   1. execution stats (db.device_health) — success-rate history per
        #      device, feeding /v1/health/devices. record_execution's own
        #      docstring says "call after each adapter.execute()"; this is it.
        #   2. passive reachability (hub.health) — reachable/unreachable + TTL.
        # A failed action records the failure but does NOT immediately mark a
        # device unreachable (a single command can fail transiently); only
        # execution-path timeouts do that. A success always refreshes reachable.
        try:
            if self._hub is not None and getattr(self._hub, "db", None) is not None:
                self._hub.db.record_execution(
                    action.device_id, action.action, success,
                    error=None if success else getattr(result, "error", None))
        except Exception:
            pass
        try:
            if self._hub is not None and getattr(self._hub, "health", None) is not None:
                if success:
                    self._hub.health.mark_reachable(action.device_id)
        except Exception:
            pass

        # INDEPENDENT-OBSERVATION (panel design 2026-07-21): if the action
        # declared a verify_with binding and it was ACCEPTED (success), read the
        # independent sensor and compare against the expected reading. This is
        # the difference between "the device accepted the command" (success) and
        # "an independent sensor confirms the effect happened" (verification).
        # Opt-in: no binding → result unchanged. Never affects success itself.
        binding = getattr(action, "verify_with", None)
        if binding is not None and success and self._hub is not None:
            try:
                result.verification = await self._verify_action(action, binding)
            except Exception as _ve:
                log.warning("verification raised for %s (recorded unverifiable): %s",
                            action.device_id, _ve)
                from .models import VerificationResult, VerificationStatus
                result.verification = VerificationResult(
                    status=VerificationStatus.UNVERIFIABLE, sensor_id=binding.sensor_id,
                    expected=binding.expected_reading, observed=None,
                    independence=("same_device" if binding.sensor_id.split(":")[0]
                                  == action.device_id else "independent_device"))
        return result

    async def _verify_action(self, action, binding):
        """Read the verifying sensor and grade the outcome. Returns a
        VerificationResult with one of verified/contradicted/unverifiable."""
        import asyncio as _asyncio
        from .models import VerificationResult, VerificationStatus

        from .adapters import AdapterExecutor

        # sensor_id: "device_id:local_sensor" (cross-device) or "device_id".
        # A sensor on a DIFFERENT device than the actuator is genuine independent
        # observation (panel, Benítez); same-device is weaker evidence.
        if ":" in binding.sensor_id:
            sensor_device, sensor_key = binding.sensor_id.split(":", 1)
        else:
            sensor_device, sensor_key = binding.sensor_id, None
        independence = ("same_device" if sensor_device == action.device_id
                        else "independent_device")

        def _result(status, observed, evidence="polled", observed_at=None):
            return VerificationResult(
                status=status, sensor_id=binding.sensor_id,
                expected=binding.expected_reading, observed=observed,
                independence=independence, evidence=evidence,
                observed_at=observed_at)

        def _pushed_reading():
            """A reading the device SENT, if the binding accepts one and it
            qualifies. Returns (value, arrived_at) or None.

            Two conditions, and the second is the one that is easy to get wrong:
            the reading must be recent, AND it must have arrived AFTER the
            action was dispatched. A reading from before the action describes
            the world before we did anything — it confirms nothing, however
            fresh it is (panel, Sosa). Comparing against the clock instead of
            against the action would accept exactly that.
            """
            window = getattr(binding, "accept_cached_within_s", None)
            if not window:
                return None
            resolver = getattr(self._hub, "resolver", None)
            if resolver is None or not hasattr(resolver, "reading_age"):
                return None
            key = sensor_key or "state"
            arrived = resolver.reading_age(sensor_device, key)
            if arrived is None:
                return None

            dispatched = getattr(action, "dispatched_at", None)
            if dispatched is not None and arrived < dispatched:
                return None          # predates the action: evidence of nothing
            if (time.time() - arrived) > float(window):
                return None          # outside the window the binding declared

            cached = resolver._state_cache.get(sensor_device) or {}
            if key not in cached:
                return None
            return cached[key], arrived

        # Read the verifying sensor through its adapter's get_state — the same
        # path the active probe uses. If we cannot reach one, the honest verdict
        # is UNVERIFIABLE (we could not look), NOT contradiction.
        def _fallback():
            """What to answer when the sensor cannot be polled.

            Tries a pushed reading first (only if the binding asked for one),
            and otherwise distinguishes two situations that used to look
            identical: a sensor that pushes ON CHANGE and stayed silent because
            nothing changed is healthy, and reporting it as `unverifiable` sends
            an operator hunting a broken sensor that is working (panel, Kim).
            The distinction is only defensible when the binding opted in — with
            no window declared, we have no basis to claim silence means anything.
            """
            pushed = _pushed_reading()
            if pushed is not None:
                value, arrived = pushed
                status = (VerificationStatus.VERIFIED
                          if value == binding.expected_reading
                          else VerificationStatus.CONTRADICTED)
                return _result(status, value, evidence="pushed", observed_at=arrived)
            if getattr(binding, "accept_cached_within_s", None):
                return _result(VerificationStatus.NO_CHANGE_REPORTED, None,
                               evidence="pushed")
            return _result(VerificationStatus.UNVERIFIABLE, None)

        inner = self._inner
        if not isinstance(inner, AdapterExecutor):
            return _fallback()

        device = self._hub.registry.get(sensor_device) if self._hub else None
        adapter = inner.get_adapter(device.adapter) if device else None
        # A push-only adapter (MQTT, GPIO) has no get_state at all — this is the
        # H2 case, and the only path that could ever reach a pushed reading.
        if adapter is None or not hasattr(adapter, "get_state"):
            return _fallback()

        try:
            state = await _asyncio.wait_for(
                adapter.get_state(sensor_device), timeout=binding.deadline_s)
        except Exception:
            state = None

        if state is None:
            return _fallback()

        observed = state.get(sensor_key) if (sensor_key and isinstance(state, dict)) else state
        if observed == binding.expected_reading:
            return _result(VerificationStatus.VERIFIED, observed)
        if sensor_key is None and isinstance(state, dict) \
                and binding.expected_reading in state.values():
            return _result(VerificationStatus.VERIFIED, state)
        return _result(VerificationStatus.CONTRADICTED, observed)


class DeviceHealth:
    """Passive device health, owned by the HUB (not the resolver).

    Health lived in StateAwareResolver until 2026-07-14, but production runs
    ExternalResolver (which lacks mark_unreachable), so the hub's
    `hasattr(resolver, "mark_unreachable")` guards silently no-op'd and NO device
    was ever marked unreachable in production — a false "everything healthy".
    Health is a property of the hub: it is populated from the EXECUTION PATH,
    which runs under any resolver, and persisted via the existing device_state
    table.

    This is PASSIVE health: it reflects the outcome of the last real interaction
    with a device (an action executed or timed out), not an active heartbeat. It
    reports what it knows — last_seen, unreachable_since, until (TTL) — and never
    asserts "powered off" (it cannot distinguish an off device from a network
    drop). Active probing is future work (DEVICE-HEALTH-ACTIVE).
    """

    DEFAULT_TTL_SECONDS = 300

    def __init__(self, hub: "DoSyncHub"):
        self._hub = hub
        self._state: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _persist(self, device_id: str) -> None:
        try:
            db = getattr(self._hub, "db", None)
            if db:
                db.save_device_state(device_id, self._state[device_id])
        except Exception as e:
            log.warning("DeviceHealth: failed to persist state for %s: %s", device_id, e)

    def mark_reachable(self, device_id: str) -> None:
        """A device responded — record last_seen and clear any unreachable mark."""
        with self._lock:
            st = self._state.get(device_id, {})
            st["last_seen"] = time.time()
            st.pop("unreachable", None)
            st.pop("unreachable_since", None)
            st.pop("unreachable_until", None)
            self._state[device_id] = st
            self._persist(device_id)

    def mark_channel(self, device_id: str, channel: str) -> None:
        """Record HOW a device last reported — the transport, not the content.

        A device whose heartbeats arrive over an unencrypted (though signed)
        channel is in a different position from one reporting over mTLS, and if
        both look identical in the device list the protocol is hiding a real
        difference (panel, Aguirre). Recorded rather than judged: it is the
        operator's decision whether that matters in their deployment.
        """
        with self._lock:
            st = self._state.get(device_id, {})
            st["report_channel"] = channel
            self._state[device_id] = st

    def record_heartbeat(self, device_id: str, reported: dict | None = None) -> None:
        """DEVICE-HEALTH-ACTIVE (b): a device proactively reported its own health.

        This is PUSH health, complementing the hub's PULL probe. It matters for
        devices the hub cannot poll — behind NAT, sleeping, on networks that
        forbid inbound connections to the device — which can still assert
        liveness by reaching out. A heartbeat is POSITIVE SIGNAL, exactly like a
        successful probe or action: it marks the device reachable (clears any
        stale unreachable mark) and stamps last_heartbeat. It never marks a
        device unreachable — a device that stops sending heartbeats is simply a
        device we have not heard from, which is weaker evidence than an action
        timing out (the same asymmetry the passive path already preserves).
        """
        with self._lock:
            st = self._state.get(device_id, {})
            now = time.time()
            st["last_seen"] = now
            st["last_heartbeat"] = now
            if reported:
                # A device may volunteer structured self-report (battery, rssi,
                # firmware…). Stored verbatim under a namespaced key; the hub
                # takes no position on its contents — it is the device's word.
                st["heartbeat_report"] = reported
            st.pop("unreachable", None)
            st.pop("unreachable_since", None)
            st.pop("unreachable_until", None)
            self._state[device_id] = st
            self._persist(device_id)

    def mark_unreachable(self, device_id: str, ttl_seconds: int | None = None) -> None:
        """A device did not respond to a real action — mark unreachable with TTL."""
        ttl = ttl_seconds if ttl_seconds is not None else self.DEFAULT_TTL_SECONDS
        with self._lock:
            st = self._state.get(device_id, {})
            now = time.time()
            st["unreachable"] = True
            st.setdefault("unreachable_since", now)   # keep the first failure time
            st["unreachable_until"] = now + ttl
            self._state[device_id] = st
            self._persist(device_id)

    def is_unreachable(self, device_id: str) -> bool:
        """True if marked unreachable and the TTL has not expired."""
        with self._lock:
            st = self._state.get(device_id)
            if not st or not st.get("unreachable"):
                return False
            if time.time() >= st.get("unreachable_until", 0):
                return False   # TTL expired — treat as unknown/recovered
            return True

    def snapshot(self, device_id: str) -> dict:
        """Honest per-device health. Never asserts 'off' — only what we observed."""
        with self._lock:
            st = dict(self._state.get(device_id, {}))
        unreachable = bool(st.get("unreachable")) and time.time() < st.get("unreachable_until", 0)
        return {
            "device_id": device_id,
            "reachable": (None if "last_seen" not in st and not unreachable
                          else (not unreachable)),
            "last_seen": st.get("last_seen"),
            "last_heartbeat": st.get("last_heartbeat"),
            "heartbeat_report": st.get("heartbeat_report"),
            # How this device last reported. `signed_plaintext` means a channel
            # that is authenticated but NOT encrypted — a real difference from
            # mTLS, and one an operator can only act on if they can see it.
            "report_channel": st.get("report_channel"),
            "unreachable_since": st.get("unreachable_since") if unreachable else None,
            "unreachable_until": st.get("unreachable_until") if unreachable else None,
            "note": ("no interaction recorded yet" if "last_seen" not in st and not unreachable
                     else ("not responding to actions since the time shown (may be powered off "
                           "or network-unreachable; passively observed)" if unreachable
                           else ("last confirmed by a device-initiated heartbeat"
                                 if st.get("last_heartbeat") == st.get("last_seen")
                                 else "responded to its last action"))),
        }

    # DEVICE-HEALTH-ACTIVE (c) — powered-off vs network-unreachable.
    # The honest core: for a UDP device (WiZ), a command timeout looks identical
    # whether the bulb has no power or its wifi dropped — UDP has no connection
    # ACK to tell them apart. So (c) does NOT guess the cause from the timeout
    # alone (that would be a workaround). It CROSS-REFERENCES the independent
    # signal we already collect — the device-initiated heartbeat (part b) — and
    # returns a verdict CALIBRATED WITH ITS EVIDENCE, admitting "indeterminate"
    # when the transport genuinely cannot tell. A device that heartbeat'd seconds
    # ago but ignores a command was alive just now (network/app issue, not
    # power); one silent for a long time AND unresponsive is more likely off; one
    # that never heartbeats at all leaves us honestly unable to say.
    _HEARTBEAT_FRESH_S = 90.0   # a heartbeat this recent means "was alive just now"

    def reachability_assessment(self, device_id: str, now: float | None = None) -> dict:
        """Best available attribution for an unreachable device, with its
        evidence and a confidence level — never a bare guess."""
        now = now if now is not None else time.time()
        snap = self.snapshot(device_id)
        if snap["reachable"] is not False:
            return {"cause": "reachable", "confidence": "n/a",
                    "evidence": "device is responding", "device_id": device_id}

        last_hb = snap.get("last_heartbeat")
        hb_age = (now - last_hb) if last_hb else None

        if hb_age is not None and hb_age < self._HEARTBEAT_FRESH_S:
            cause, conf, why = ("network_or_app", "high",
                f"heartbeat {hb_age:.0f}s ago but not responding to actions — the device "
                f"was alive just now, so this is a network or application fault, not power")
        elif last_hb is not None:
            cause, conf, why = ("likely_powered_off", "medium",
                f"no heartbeat for {hb_age:.0f}s and unresponsive — the device stopped "
                f"reaching out, consistent with loss of power (but a prolonged network "
                f"outage cannot be fully excluded)")
        else:
            cause, conf, why = ("indeterminate", "low",
                "device never sent a heartbeat, so power and network cannot be "
                "distinguished from a command timeout alone (transport limitation)")
        return {"cause": cause, "confidence": conf, "evidence": why,
                "device_id": device_id,
                "last_heartbeat_age_s": round(hb_age, 1) if hb_age is not None else None,
                "unreachable_since": snap.get("unreachable_since")}

    def load_from_db(self) -> None:
        try:
            db = getattr(self._hub, "db", None)
            if db:
                with self._lock:
                    self._state = {k: v for k, v in db.load_all_device_states().items()}
        except Exception as e:
            log.warning("DeviceHealth: failed to load state from db: %s", e)
