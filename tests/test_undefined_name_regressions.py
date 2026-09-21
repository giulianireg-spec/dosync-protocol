"""Behaviour behind two undefined-name bugs that failed silently.

Both lived inside try/except blocks, so a normal run showed nothing:

- restore.py used time.time() without importing time. Every presence signal
  failed to restore on startup, so occupancy started empty after each restart.
- execution.py used the optional metrics module (_M) without importing it, so
  action_execution_seconds never recorded a single action.

The static check in test_no_undefined_names.py catches the class of bug; these
pin the behaviour each one broke.
"""
import asyncio
import os
import tempfile

from dosync.hub import DoSyncHub
from dosync.models import ContextSignalType, PresenceSignal


def test_presence_signals_survive_a_restart():
    db = os.path.join(tempfile.mkdtemp(), "presence.db")
    hub = DoSyncHub(db_path=db)
    hub.update_presence(PresenceSignal(
        device_id="pir-1", signal_type=list(ContextSignalType)[0],
        present=True, confidence=0.9))
    assert len(hub.occupancy._signals) == 1

    restarted = DoSyncHub(db_path=db)
    assert len(restarted.occupancy._signals) == 1, (
        "presence signals were not restored after a restart")


def test_an_executed_action_is_recorded_in_the_metrics():
    from dosync import metrics
    from dosync.execution import _TimedExecutor

    def observed():
        return sum(rec[2] for rec in metrics.action_execution_seconds._data.values())

    class _Inner:
        async def execute(self, action, urgency):
            class _Result:
                success = True
            return _Result()

    class _Action:
        device_id = "dev-1"
        action = "turn_on"

    before = observed()
    asyncio.run(_TimedExecutor(_Inner()).execute(_Action(), None))
    assert observed() == before + 1, "the action's execution time was not recorded"
