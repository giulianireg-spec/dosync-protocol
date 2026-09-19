"""The telemetry bridge.

The eighth of the eleven responsibilities extracted from `hub.py`: the generic
bridge between a telemetry-emitting adapter and the operation state machine. It
is device-agnostic -- it speaks only the abstract TelemetryEvent vocabulary and
knows nothing of MAVLink, drones or flight -- and it depends on nothing of the
hub beyond the database and the audit log, which it takes as arguments.

Re-exported through `hub.apply_telemetry`, so every existing caller keeps
working unchanged.
"""
from __future__ import annotations

import logging

log = logging.getLogger("dosync.hub")


def apply_telemetry(db, audit_log, device_id: str, event, reason: str = "",
                    phase: str = None, now: float = None) -> dict:
    """Apply one telemetry fact to a device's active operation.

    The GENERIC bridge between any telemetry-emitting adapter and the operation
    state machine -- the first thing to actually drive the reconciler. It is
    deliberately device-agnostic: it speaks only the abstract TelemetryEvent
    vocabulary, knows nothing of MAVLink, drones, or flight. An oven that one day
    emits telemetry would call this identically. The adapter's job is to
    translate its device-native signal into a TelemetryEvent; the job here is to
    find the operation, reconcile, persist, and audit.

    Flow:
      1. Find the device's single active (non-terminal) operation.
      2. Rehydrate it faithfully from the DB (preserving time_in_state/history).
      3. Let the stateless reconciler apply the fact (telemetry wins; illegal
         or duplicate facts are no-ops, never crashes).
      4. Persist the (possibly unchanged) operation and audit the outcome.

    Returns a small dict describing what happened -- changed/no-op, the from/to
    states, and a note -- so the caller (and tests) can see the result without
    re-reading the DB. Returns {"matched": False} when the device has no active
    operation (a stray telemetry packet for an idle device is not an error -- it
    is simply ignored).

    Safety note: a telemetry fact is the ONLY thing that advances an operation
    toward completion. Silence never does. A disconnection is not a fact and must
    not be passed here as one -- it does not advance anything.
    """
    from .operations import Operation
    from .reconciler import OperationReconciler, TelemetryEvent

    if isinstance(event, str):
        event = TelemetryEvent(event)

    # 1. Find the device's active operation. There should be at most one
    #    non-terminal operation per device at a time; if several exist (a
    #    pathological state), reconcile the most recent, which get_active_
    #    operations returns first (ORDER BY created_at DESC).
    active = [o for o in db.get_active_operations()
              if o.get("device_id") == device_id]
    if not active:
        log.debug("apply_telemetry: no active operation for %s (event '%s' ignored)",
                  device_id, event.value)
        return {"matched": False, "device_id": device_id, "event": event.value}

    op_dict = active[0]
    op = Operation.from_dict(op_dict)

    # Carry an updated sub-phase if the adapter reported one (e.g. "arming").
    # The protocol never interprets this string; it is domain detail.
    if phase is not None:
        op.phase = phase

    # 2 + 3. Reconcile -- pure logic, decides the state change (or no-op).
    reconciler = OperationReconciler()
    result = reconciler.reconcile(op, event, reason=reason, now=now)

    # 4. Persist (even a no-op may have updated `phase`) and audit.
    db.save_operation(op.to_dict(), terminal=op.is_terminal)
    audit_log.append({
        "type":         "operation_telemetry",
        "operation_id": op.operation_id,
        "device_id":    device_id,
        "event":        event.value,
        "changed":      result.changed,
        "from_state":   result.from_state.value,
        "to_state":     result.to_state.value,
        "note":         result.note,
    })

    return {
        "matched":      True,
        "operation_id": op.operation_id,
        "device_id":    device_id,
        "event":        event.value,
        "changed":      result.changed,
        "from_state":   result.from_state.value,
        "to_state":     result.to_state.value,
        "note":         result.note,
    }
