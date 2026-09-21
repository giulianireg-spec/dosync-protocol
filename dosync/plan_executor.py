"""The plan execution engine.

The ninth of the eleven responsibilities extracted from `hub.py`: how a resolved
plan is actually run against devices -- policy-aware execution, the parallel /
abort / retry strategies, parameter validation, splitting a plan by execution
model, and starting long-running (telemetry-confirmed) actions.

It is not the device executor (that is `_TimedExecutor` in execution.py) and not
device health (`DeviceHealth`, same file): it is the layer above them that turns
one resolved ActionPlan into dispatched work. It depends on five hub services --
the resolver, device health, the audit log, the capability registry and the
database -- taken here as constructor arguments.

The methods are unchanged; only their address is. `DoSyncHub` owns a PlanExecutor
and delegates the entry points (execute_intent's calls, the composite section's
`_action_execution_model`, and three tests' `_validate_plan_params`) to it, so
every caller keeps working. The parallel / abort / retry strategies are internal
here and are not delegated.
"""
from __future__ import annotations

import asyncio
import time
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation only
    from .models import DeviceAction

# "dosync.hub" and not "dosync.plan_executor": these records went to that logger
# before the move, and an operator filtering on it would otherwise stop seeing
# them the day the file changed.
log = logging.getLogger("dosync.hub")


class PlanExecutor:
    def __init__(self, resolver, health, audit_log, registry, db):
        self.resolver = resolver
        self.health = health
        self.audit_log = audit_log
        self.registry = registry
        self.db = db
        self.progress_cb_failures = 0

    async def _execute_with_policy_cb(self, plan, executor, intent, progress_cb=None):
        """MCP-V13: wrap the executor so each completed action can be published as
        partial progress WITHOUT changing any strategy signature. The wrapper
        fires progress_cb(result) as each action resolves; the strategies below
        are untouched. progress_cb is best-effort — a failing callback must never
        affect execution (an observer cannot break the observed)."""
        if progress_cb is not None:
            _inner = executor
            _hub = self

            class _ProgressExecutor:
                def __getattr__(self, n): return getattr(_inner, n)
                async def execute(self, action, urgency):
                    r = await _inner.execute(action, urgency)
                    try:
                        progress_cb(r)
                    except Exception as _cb_e:
                        _hub.progress_cb_failures += 1
                        log.warning("progress_cb raised (ignored, count=%d): %s",
                                    _hub.progress_cb_failures, _cb_e)
                    return r
            executor = _ProgressExecutor()
        return await self._execute_with_policy(plan, executor, intent)

    async def _execute_with_policy(self, plan, executor, intent):
        """Dispatch to the correct execution strategy based on failure_policy.
        Emergency intents always force CONTINUE — protocol-level guarantee."""
        from .models import FailurePolicy, Urgency
        policy = plan.failure_policy or FailurePolicy.CONTINUE
        if intent.urgency == Urgency.EMERGENCY:
            if policy == FailurePolicy.ABORT:
                log.info("FailurePolicy.ABORT overridden to CONTINUE for emergency '%s'", intent.intent)
            policy = FailurePolicy.CONTINUE
        if policy == FailurePolicy.ABORT:
            r, f, a = await self._execute_abort(plan.actions, executor, intent)
            return r, f, a, "abort"
        elif policy == FailurePolicy.RETRY:
            max_r = plan.max_retries if plan.max_retries else 1
            if intent.urgency == Urgency.EMERGENCY:
                max_r = 1
            r, f, a = await self._execute_retry(plan.actions, executor, intent, max_r)
            return r, f, a, "retry"
        else:
            r, f, a = await self._execute_parallel(plan.actions, executor, intent)
            return r, f, a, "continue"

    async def _execute_parallel(self, actions, executor, intent):
        """CONTINUE: execute all actions in parallel, failures never stop execution."""
        import os as _os
        from .models import ActionResult
        _t = float(_os.environ.get("DOSYNC_INTENT_TIMEOUT",
                   "5.0" if intent.urgency.value == "emergency" else "10.0"))
        tasks = {asyncio.ensure_future(executor.execute(a, intent.urgency)): a for a in actions}
        results = []
        if tasks:
            done, pending = await asyncio.wait(tasks.keys(), timeout=_t)
            for fut in done:
                results.append(fut.result())
            for fut in pending:
                action = tasks[fut]
                log.warning("Timeout: %s/%s after %.1fs", action.device_id, action.action, _t)
                self.health.mark_unreachable(action.device_id)
                if hasattr(self.resolver, "mark_unreachable"):
                    self.resolver.mark_unreachable(action.device_id)
                results.append(ActionResult(device_id=action.device_id, action=action.action,
                                            success=False, error=f"timeout after {_t}s"))
                fut.cancel()
        return results, [r.device_id for r in results if not r.success], []

    async def _execute_abort(self, actions, executor, intent):
        """ABORT: execute in batches by relevance_score. Cancel remaining if any batch fails."""
        import os as _os
        from .models import ActionResult
        _t = float(_os.environ.get("DOSYNC_INTENT_TIMEOUT",
                   "5.0" if intent.urgency.value == "emergency" else "10.0"))
        sorted_actions = sorted(actions, key=lambda a: a.relevance_score, reverse=True)
        batches = [sorted_actions[i:i+5] for i in range(0, len(sorted_actions), 5)]
        all_results, aborted = [], []
        abort_triggered = False
        for idx, batch in enumerate(batches):
            if abort_triggered:
                for a in batch:
                    aborted.append(a.device_id)
                    all_results.append(ActionResult(device_id=a.device_id, action=a.action,
                        success=False, error="aborted — prior batch failed", aborted=True))
                continue
            tasks = {asyncio.ensure_future(executor.execute(a, intent.urgency)): a for a in batch}
            done, pending = await asyncio.wait(tasks.keys(), timeout=_t)
            batch_results = [fut.result() for fut in done]
            for fut in pending:
                a = tasks[fut]
                self.health.mark_unreachable(a.device_id)
                if hasattr(self.resolver, "mark_unreachable"):
                    self.resolver.mark_unreachable(a.device_id)
                batch_results.append(ActionResult(device_id=a.device_id, action=a.action,
                    success=False, error=f"timeout after {_t}s"))
                fut.cancel()
            all_results.extend(batch_results)
            if any(not r.success for r in batch_results):
                log.warning("ABORT triggered after batch %d/%d — failures: %s",
                    idx+1, len(batches), [r.device_id for r in batch_results if not r.success])
                abort_triggered = True
        failed = [r.device_id for r in all_results if not r.success and not r.aborted]
        return all_results, failed, aborted

    async def _execute_retry(self, actions, executor, intent, max_retries):
        """RETRY: retry each failed action up to max_retries with exponential backoff."""
        import os as _os
        from .models import ActionResult
        _t = float(_os.environ.get("DOSYNC_INTENT_TIMEOUT",
                   "5.0" if intent.urgency.value == "emergency" else "10.0"))

        async def _with_retry(action):
            last = None
            for attempt in range(max_retries + 1):
                if attempt > 0:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    log.info("RETRY %d/%d for %s (backoff %.1fs)", attempt, max_retries,
                             action.device_id, backoff)
                    await asyncio.sleep(backoff)
                try:
                    r = await asyncio.wait_for(executor.execute(action, intent.urgency), timeout=_t)
                    r.retries = attempt
                    if r.success:
                        return r
                    last = r
                except asyncio.TimeoutError:
                    self.health.mark_unreachable(action.device_id)
                    if hasattr(self.resolver, "mark_unreachable"):
                        self.resolver.mark_unreachable(action.device_id)
                    last = ActionResult(device_id=action.device_id, action=action.action,
                        success=False, error=f"timeout (attempt {attempt+1}/{max_retries+1})",
                        retries=attempt)
            log.warning("RETRY exhausted for %s after %d attempt(s)", action.device_id, max_retries+1)
            return last or ActionResult(device_id=action.device_id, action=action.action,
                success=False, error=f"exhausted {max_retries} retries", retries=max_retries)

        results = list(await asyncio.gather(*[_with_retry(a) for a in actions]))
        return results, [r.device_id for r in results if not r.success], []

    def _validate_plan_params(self, plan, intent):
        """Validate each action's params against its actuator's JSON Schema.

        Returns (filtered_plan, rejected). Valid actions stay in the plan; each
        invalid action is dropped and recorded — both in the returned `rejected`
        list and in the audit log — so nothing is silently discarded.

        Rejection here means "the mind asked for something the actuator declared
        it does not accept" — distinct from a device that fails to respond at
        execution time. The audit entry type makes that distinction explicit.
        """
        from .models import ActionPlan as _AP
        from .validation import validate_params

        valid, rejected = [], []
        for action in plan.actions:
            device = self.registry.get(action.device_id)
            schema = None
            if device:
                for act in device.actuators:
                    if act.type == action.action:
                        schema = getattr(act, "params_schema", None)
                        break
            # No device or no schema for this action → nothing to validate against.
            if not schema:
                valid.append(action)
                continue

            ok, err = validate_params(schema, action.params or {})
            if ok:
                valid.append(action)
            else:
                rejected.append((action, err))
                log.warning(
                    "Action rejected by param validation: %s.%s — %s",
                    action.device_id, action.action, err,
                )
                self.audit_log.append({
                    "type":      "action_rejected_invalid_params",
                    "intent_id": intent.intent_id,
                    "intent":    intent.intent.value,
                    "device_id": action.device_id,
                    "action":    action.action,
                    "params":    action.params,
                    "reason":    err,
                    "source":    getattr(intent, "source", "api"),
                })

        filtered = _AP(intent_id=plan.intent_id, actions=valid, urgency=plan.urgency,
                       failure_policy=getattr(plan, "failure_policy", None),
                       max_retries=getattr(plan, "max_retries", 1))
        return filtered, rejected

    # ── Long-running operations: split + write-ahead (execution_model) ────────
    # This is a SELF-CONTAINED sub-protocol layered on top of intent execution.
    # The instant path (every existing action) is untouched: these helpers only
    # ever handle actions whose actuator declares execution_model == "long_running".
    # An implementer in another language can read this block as one unit.

    def _action_execution_model(self, action: "DeviceAction") -> tuple[str, bool]:
        """Return (execution_model, emits_telemetry) for an action by looking up
        its actuator in the device manifest. Defaults to ("instant", False) when the
        device/actuator is unknown — an unknown action is treated as instant, never
        long-running, so a missing manifest can never strand an operation."""
        manifest = self.registry.get(action.device_id)
        if not manifest:
            return ("instant", False)
        for act in manifest.actuators:
            if act.type == action.action:
                return (getattr(act, "execution_model", "instant"),
                        getattr(act, "emits_telemetry", False))
        return ("instant", False)

    def _split_plan_by_execution_model(self, plan):
        """Split a plan's actions into (instant_actions, long_running_actions).
        Each action goes to exactly one list — no action is ever in both."""
        instant, long_running = [], []
        for action in plan.actions:
            model, _ = self._action_execution_model(action)
            (long_running if model == "long_running" else instant).append(action)
        return instant, long_running

    async def _start_long_running_actions(self, long_running_actions, executor, intent):
        """For each long-running action: WRITE-AHEAD (create + persist the operation
        in `pending` BEFORE dispatching), then dispatch, then transition by the
        dispatch result. Returns a list of {operation_id, device_id, state} for the
        IntentResult. Never blocks waiting for the action to finish — it only starts.

        Panel rules honored here:
          - write-ahead: persist `pending` before dispatch, so a crash mid-dispatch
            never leaves a running device with no operation record.
          - silence != success: a successful DISPATCH means the device ACCEPTED the
            command, not that it finished. For a telemetry device the operation waits
            in `in_progress` for telemetry to confirm/advance; for a core device
            (no telemetry) the successful dispatch is the only signal there will be,
            so it goes to `in_progress` and a later positive signal completes it.
          - graceful degradation: an adapter that doesn't cooperate just returns a
            normal ActionResult; the operation is resolved from it. No adapter is
            required to understand operations.
        """
        from .operations import Operation, OperationState

        started = []
        for action in long_running_actions:
            _, emits_telemetry = self._action_execution_model(action)
            op = Operation(
                device_id=action.device_id,
                action=action.action,
                telemetry_capable=emits_telemetry,
            )
            # WRITE-AHEAD: persist in `pending` before we touch the device.
            self.db.save_operation(op.to_dict(), terminal=op.is_terminal)
            self.audit_log.append({
                "type":         "operation_created",
                "operation_id": op.operation_id,
                "intent_id":    intent.intent_id,
                "device_id":    action.device_id,
                "action":       action.action,
                "state":        op.state.value,
            })

            # Dispatch (start the action). We do NOT wait for it to finish.
            try:
                result = await executor.execute(action, intent.urgency)
                dispatch_ok = bool(getattr(result, "success", False))
                err = getattr(result, "error", None)
            except Exception as e:  # an adapter blowing up must not strand the op
                dispatch_ok = False
                err = str(e)

            if dispatch_ok:
                # Accepted by the device. NOT completed — started.
                op.transition_to(OperationState.IN_PROGRESS,
                                 reason="dispatch accepted by device")
            else:
                # The device refused / dispatch failed → the operation never ran.
                op.transition_to(OperationState.FAILED,
                                 reason=f"dispatch failed: {err}" if err else "dispatch failed")

            self.db.save_operation(op.to_dict(), terminal=op.is_terminal)
            self.audit_log.append({
                "type":         "operation_transition",
                "operation_id": op.operation_id,
                "intent_id":    intent.intent_id,
                "from_state":   "pending",
                "to_state":     op.state.value,
            })
            started.append({
                "operation_id": op.operation_id,
                "device_id":    action.device_id,
                "state":        op.state.value,
            })
        return started
