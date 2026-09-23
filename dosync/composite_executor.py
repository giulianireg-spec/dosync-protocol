"""Composite-intent orchestration -- the nervous system.

A composition intent (e.g. inspect_area) does not resolve to a flat parallel
ActionPlan. It composes an ORDERED SEQUENCE of atomic operations that the
OperationSupervisor drives in a closed loop -- dispatch a step, wait for its
confirmed arrival via reconciled telemetry, then the next, reacting to guards
each tick. This is the brain coordinating the body, not fire-and-forget.

The pieces (all built and tested independently):
  RouteComposer        -> the ordered steps (geometry)
  CompositeOperation   -> the structure that holds them
  OperationSupervisor  -> the closed-loop driver
  OperationGuards      -> real-time in-flight monitoring

The tenth of the eleven responsibilities extracted from `hub.py`: the layer that
WIRES those pieces to the live hub's services -- the PolicyEngine, the audit log,
the database, and the plan executor's action-execution-model classifier -- which
it reads from the hub at the moment of use. `DoSyncHub` owns a CompositeExecutor and
delegates execute_composite_intent and _route_composite_intent to it, so every
caller keeps working. The methods are unchanged; only their address is.
"""
from __future__ import annotations

import logging

from .models import IntentResult

log = logging.getLogger("dosync.hub")


class CompositeExecutor:
    def __init__(self, hub):
    # The hub, not its services. server.py builds the hub first and installs
    # some of them afterwards -- `hub.policy_engine = policy_engine` always, and
    # `hub.resolver = ExternalResolver(...)` when an external resolver is
    # configured. Values captured here at construction froze the ones the hub
    # started with: the policy engine was None, so every composite step was
    # dispatched with no policy at all -- a 100 m geofence let a 1000 m waypoint
    # through. Each service is read from the hub at the moment of use, as the
    # code did before it moved out of hub.py.
        self._hub = hub

    audit_log     = property(lambda self: self._hub.audit_log)
    db            = property(lambda self: self._hub.db)
    policy_engine = property(lambda self: self._hub.policy_engine)
    _action_execution_model = property(lambda self: self._hub._action_execution_model)

    async def _dispatch_composite_step(self, step, executor, intent):
        """Dispatch ONE composite step as a long-running atomic operation, AFTER the
        PolicyEngine admits it. Returns the operation_id the supervisor will watch.

        CRITICAL (panel): the composition path does NOT bypass admission security.
        Every step is evaluated by the PolicyEngine first — the admission geofence,
        rate limits, manual-control lockout, everything — exactly as a normal intent
        is. A blocked step raises, the supervisor sees the failed dispatch and aborts.

        Reuses the write-ahead pattern of _start_long_running_actions: persist the
        operation in `pending` BEFORE touching the device, dispatch, transition by the
        result. The operation then advances to `completed` later, when telemetry
        confirms arrival (apply_telemetry) — which is what the supervisor polls for.
        """
        from .operations import Operation, OperationState
        from .models import DeviceAction, ActionPlan as _AP

        action = DeviceAction(
            device_id=step.device_id, action=step.action, params=dict(step.params or {}))

        # ── PolicyEngine admission for THIS step ──────────────────────────────
        if self.policy_engine:
            from .policies import PolicyDecision
            single_plan = _AP(intent_id=intent.intent_id, actions=[action],
                              urgency=intent.urgency)
            result = self.policy_engine.evaluate(intent, single_plan)
            if result.decision == PolicyDecision.BLOCK:
                self.audit_log.append({
                    "type":      "composite_step_blocked",
                    "intent_id": intent.intent_id,
                    "device_id": step.device_id,
                    "action":    step.action,
                    "policy":    result.policy_name,
                    "reason":    result.reason,
                })
                raise PermissionError(
                    f"step '{step.action}' blocked by policy '{result.policy_name}': "
                    f"{result.reason}")
            if result.decision == PolicyDecision.MODIFY and result.modified_actions:
                action = result.modified_actions[0]

        # ── Write-ahead: persist `pending` before dispatch ───────────────────
        _, emits_telemetry = self._action_execution_model(action)
        op = Operation(device_id=action.device_id, action=action.action,
                       telemetry_capable=emits_telemetry)
        self.db.save_operation(op.to_dict(), terminal=op.is_terminal)
        self.audit_log.append({
            "type": "operation_created", "operation_id": op.operation_id,
            "intent_id": intent.intent_id, "device_id": action.device_id,
            "action": action.action, "state": op.state.value,
            "composite": True,
        })

        # ── Dispatch (start the step). Do NOT wait for it to finish here ──────
        try:
            res = await executor.execute(action, intent.urgency)
            dispatch_ok = bool(getattr(res, "success", False))
            err = getattr(res, "error", None)
        except Exception as e:
            dispatch_ok, err = False, str(e)

        if dispatch_ok:
            op.transition_to(OperationState.IN_PROGRESS, reason="dispatch accepted")
        else:
            op.transition_to(OperationState.FAILED,
                             reason=f"dispatch failed: {err}" if err else "dispatch failed")
        self.db.save_operation(op.to_dict(), terminal=op.is_terminal)
        self.audit_log.append({
            "type": "operation_transition", "operation_id": op.operation_id,
            "intent_id": intent.intent_id, "from_state": "pending",
            "to_state": op.state.value, "composite": True,
        })
        return op.operation_id

    def _read_operation_state(self, operation_id):
        """Read the reconciled atomic state of an operation (the state telemetry
        keeps current). The supervisor polls THIS — never the raw telemetry queue."""
        row = self.db.get_operation(operation_id)
        return row.get("state") if row else None

    def _build_guard_context_provider(self, device_id):
        """Build the GuardContext provider the supervisor calls each tick.

        HONEST SCOPE (panel): it fills what the CURRENT data allows and leaves the
        rest None. The guards abstain on None (verified), so a guard whose signal is
        not yet flowing simply does not fire — no dead code, no false promise.

        Active with real data TODAY:
          - manual_control_active: from the device's operation reaching `interrupted`
            (a human took control — apply_telemetry sets this from MANUAL_CONTROL_TAKEN)
          - seconds_in_step: from the active operation's time_in_state

        Waiting on continuous position/battery telemetry (guards already built+tested,
        activate automatically once the provider starts filling these):
          - lat/lon/alt  -> GeofenceGuard in flight
          - battery_percent -> BatteryGuard
        A future step publishes a per-device telemetry snapshot; until then these stay
        None and their guards correctly abstain.
        """
        from .operation_guards import GuardContext

        def provider(comp):
            manual = False
            seconds_in_step = None
            try:
                for op in self.db.get_active_operations():
                    if op.get("device_id") != device_id:
                        continue
                    if op.get("state") == "interrupted":
                        manual = True
                    entered = op.get("state_entered_at")
                    if entered is not None:
                        import time as _t
                        seconds_in_step = _t.time() - entered
            except Exception:
                pass  # never let context-building crash the supervisor
            return GuardContext(
                device_id=device_id,
                manual_control_active=manual,
                seconds_in_step=seconds_in_step,
                # lat/lon/alt/battery/seconds_since_telemetry: None until a
                # continuous telemetry snapshot feeds them (guards abstain on None).
            )
        return provider

    async def execute_composite_intent(self, intent, executor, context, guard_set=None,
                                       config=None):
        """Execute a composition intent (e.g. inspect_area) as a supervised, closed-
        loop sequence. Composes the route, builds the CompositeOperation, wires the
        supervisor to the live hub (PolicyEngine-gated dispatch, reconciled-state
        polling, guard provider), and runs it.

        Args:
          intent:   the composition Intent (its .context carries center/radius/etc.)
          executor: the device executor (dispatches atomic steps)
          context:  the resolution context for the composer (center, radius_m, ...)
          guard_set: an optional OperationGuards.GuardSet; if None, no guards beyond
                     the supervisor's own stall backstop.
          config:   optional SupervisorConfig (poll cadence, step timeout).

        Returns the terminal CompositeState.
        """
        from .route_composer import RouteComposer
        from .composite_operations import CompositeOperation
        from .operation_supervisor import OperationSupervisor

        device_id = context.get("device_id")
        if not device_id:
            raise ValueError("execute_composite_intent requires context['device_id']")

        # 1. Compose the ordered steps (geometry) — the cerebellum's translation.
        steps = RouteComposer().compose_inspect_area(device_id, context)

        # 2. The structure that holds and tracks them.
        comp = CompositeOperation(device_id=device_id, intent=intent.intent.value,
                                  steps=steps, context=context)
        self.audit_log.append({
            "type": "composite_started", "composite_id": comp.composite_id,
            "intent_id": intent.intent_id, "intent": intent.intent.value,
            "device_id": device_id, "steps": len(steps),
        })

        # 3. Wire the supervisor to the live hub.
        async def dispatch(step):
            return await self._dispatch_composite_step(step, executor, intent)

        guard_fn = None
        if guard_set is not None:
            guard_fn = guard_set.make_guard_fn(
                self._build_guard_context_provider(device_id))

        supervisor = OperationSupervisor(
            dispatch=dispatch,
            read_state=self._read_operation_state,
            guard=guard_fn,
            config=config,
        )

        # 4. Run the closed loop to a terminal mission state.
        final = await supervisor.run(comp)

        self.audit_log.append({
            "type": "composite_finished", "composite_id": comp.composite_id,
            "intent_id": intent.intent_id, "final_state": final.value,
            "steps_completed": sum(1 for s in comp.steps if s.done),
            "steps_total": len(comp.steps),
        })
        return final

    async def _route_composite_intent(self, intent, executor, composition_kind):
        """Route a composition intent to the right composer and run it as a closed-
        loop supervised sequence, wrapping the terminal CompositeState in the
        IntentResult the caller expects.

        The selection is a simple `if`, not a generic registry (panel: no premature
        abstraction with one composer). An UNKNOWN kind fails EXPLICITLY — a declared
        composition the hub cannot compose is a configuration error that must shout,
        never a silent fall-through to the flat path.

        The composition context (center, radius_m, device_id, ...) travels in
        intent.context — the same place every intent carries its context.
        """
        if composition_kind != "perimeter":
            # Explicit failure — never silently degrade to the flat path.
            log.error("Intent '%s' declares composition_kind='%s' but no composer "
                      "handles it.", intent.intent.value, composition_kind)
            self.audit_log.append({
                "type": "composite_unknown_kind",
                "intent_id": intent.intent_id,
                "intent": intent.intent.value,
                "composition_kind": composition_kind,
            })
            return IntentResult(
                intent_id=intent.intent_id, success=False, results=[],
                failed_devices=[], status="failed",
            )

        from .composite_operations import CompositeState
        try:
            final_state = await self.execute_composite_intent(
                intent, executor, context=intent.context)
        except ValueError as e:
            # e.g. missing device_id / center in the context.
            log.error("Composite intent '%s' rejected: %s", intent.intent.value, e)
            self.audit_log.append({
                "type": "composite_rejected",
                "intent_id": intent.intent_id,
                "intent": intent.intent.value,
                "reason": str(e),
            })
            return IntentResult(
                intent_id=intent.intent_id, success=False, results=[],
                failed_devices=[], status="failed",
            )

        # Map the terminal CompositeState to the IntentResult contract.
        success = final_state == CompositeState.COMPLETED
        return IntentResult(
            intent_id=intent.intent_id, success=success, results=[],
            failed_devices=[], status=final_state.value,
        )
