"""The collaborators extracted from hub.py see services installed after the hub is built.

server.py builds the hub first and installs services afterwards:
`hub.policy_engine = policy_engine` always (the hub starts with None), and
`hub.resolver = ExternalResolver(...)` when an external resolver is configured.

The 9th-11th extractions passed those services to PlanExecutor, CompositeExecutor
and StateRefresher as constructor arguments, which froze the values the hub
started with. CompositeExecutor held policy_engine=None, so from the 10th
extraction on every composite step on the reference hub was dispatched with no
policy -- a 100 m geofence let a 1000 m waypoint through. The only test that
covered it asserted through a check() helper that printed instead of failing, so
the suite stayed green. Before the move, the code read self.policy_engine from
the hub on every use; the collaborators now do the same.
"""
import pytest

from dosync.hub import DoSyncHub
from dosync.composite_operations import CompositeState
from dosync.operation_supervisor import SupervisorConfig
from dosync.policies import GeofencePolicy, PolicyEngine

import tests.test_composite_orchestration as orch

SERVICES = {
    "_plan_executor":      ["resolver", "health", "audit_log", "registry", "db"],
    "_composite_executor": ["audit_log", "db", "policy_engine"],
    "_state_refresher":    ["resolver", "health", "registry"],
}


@pytest.mark.parametrize("collaborator,service",
                         [(c, s) for c, svcs in SERVICES.items() for s in svcs])
def test_a_service_replaced_on_the_hub_is_seen_by_the_collaborator(collaborator, service):
    hub = DoSyncHub(db_path=":memory:")
    replacement = object()
    setattr(hub, service, replacement)
    assert getattr(getattr(hub, collaborator), service) is replacement, (
        f"{collaborator} still holds the {service} the hub was built with")


def test_a_geofence_installed_after_the_hub_is_built_blocks_a_composite_step():
    hub = orch._make_hub()              # installs a fresh PolicyEngine, as server.py does
    hub.policy_engine.add(GeofencePolicy(orch.CLAT, orch.CLON, max_radius_m=100.0))
    final = orch._run(hub.execute_composite_intent(
        orch._intent(), orch._SimExecutor(hub), orch._ctx(radius_m=1000),
        config=SupervisorConfig(poll_interval_s=0.001, step_timeout_s=5.0)))

    assert final == CompositeState.ABORTED, \
        "a 1000 m waypoint was flown inside a 100 m geofence"
    assert "composite_step_blocked" in [e.get("type") for e in hub.audit_log.entries()]
