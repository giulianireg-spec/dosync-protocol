"""
DoSync — deployment policy configuration (POL-1).
=================================================

Device preferences are DEPLOYMENT configuration, not protocol and not reference-hub
code (panel decision 2026-07-12, DoSync-Panel-Frontera-Deployment). The protocol
defines HOW intent maps to capability; WHICH devices exist and WHAT preferences
apply is the deployer's business — exactly as HTTP does not know which URLs live on
your server.

Until this module existed the principle was theory. `server.py` hard-coded one
deployment's choices into the reference hub:

    policy_engine.add(NeverAfterHoursPolicy(
        actuator_types=["unlock", "alarm"],
        blocked_hours_start=0, blocked_hours_end=6, ...))

Who decided 00:00–06:00? One house. Every hub running this code inherited it, and
changing it meant editing the reference implementation — which, as Sosa put it,
means it was never configuration at all. Worse, policies that carry no deployment
values but only make sense for some deployments (GeofencePolicy: "each deployment
configures its own perimeter via the constructor", says its own docstring) could
not be registered AT ALL without forking the hub.

This module loads them from a file the deployer owns:

    DOSYNC_POLICIES=/etc/dosync/policies.json  (or --policies)

    {
      "version": 1,
      "policies": [
        {"type": "never_after_hours",
         "actuator_types": ["unlock", "alarm"],
         "blocked_hours_start": 0, "blocked_hours_end": 6,
         "reason": "No remote unlocking overnight"}
      ]
    }

Being a file makes these shareable, forkable and versionable between deployments —
the ecosystem of shared configurations the project wants to enable without having
to curate it.

FAIL LOUDLY, ON PURPOSE
-----------------------
Every error here raises. A policy is usually a RESTRICTION: "do not unlock at
night", "confirm before the alarm", "never let the drone past this perimeter". A
typo that silently skips one leaves the operator believing they are protected when
they are not — strictly worse than refusing to start. So: unknown type, bad
argument, missing file when one was configured — all raise. Silence is the failure
mode this protocol has been paying for all along.

Infrastructure policies (rate limits, conflict resolution, contextual weighting)
are NOT loaded here: they carry no deployment values and are part of what the
reference hub is. Only policies expressing a deployment's own choices live in this
file.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .policies import (BasePolicy, BlockIntentPolicy, DeviceExclusionPolicy,
                       GeofencePolicy, ManualControlActivePolicy,
                       NeverAfterHoursPolicy, RequireConfirmationPolicy)

if TYPE_CHECKING:
    from .hub import DoSyncHub

log = logging.getLogger("dosync.policy_config")

CONFIG_VERSION = 1

# type string -> (class, needs_hub)
# Only deployment-expressing policies. Adding one here is the single step needed
# to make it configurable; nothing else in the loader changes.
POLICY_TYPES: dict[str, tuple[type[BasePolicy], bool]] = {
    "never_after_hours":     (NeverAfterHoursPolicy,     False),
    "require_confirmation":  (RequireConfirmationPolicy, False),
    "device_exclusion":      (DeviceExclusionPolicy,     False),
    "block_intent":          (BlockIntentPolicy,         False),
    "geofence":              (GeofencePolicy,            False),
    "manual_control_active": (ManualControlActivePolicy, True),
}


class PolicyConfigError(Exception):
    """A deployment policy file could not be loaded. Never swallowed."""


def _build_one(index: int, entry: dict, hub: "DoSyncHub | None") -> BasePolicy:
    if not isinstance(entry, dict):
        raise PolicyConfigError(f"policies[{index}]: expected an object, got {type(entry).__name__}")

    ptype = entry.get("type")
    if not ptype:
        raise PolicyConfigError(f"policies[{index}]: missing required field 'type'")

    if ptype not in POLICY_TYPES:
        known = ", ".join(sorted(POLICY_TYPES))
        raise PolicyConfigError(
            f"policies[{index}]: unknown policy type {ptype!r}. Known types: {known}. "
            "Refusing to start rather than silently skip a policy you asked for."
        )

    cls, needs_hub = POLICY_TYPES[ptype]
    # Keys starting with "_" are metadata, not arguments. JSON has no comments,
    # and a policy file MUST be documentable: a restriction whose reason nobody
    # recorded is one nobody dares to remove later. "_why", "_owner", "_ticket"
    # and friends are for humans and are ignored here.
    kwargs = {k: v for k, v in entry.items()
              if k != "type" and not k.startswith("_")}

    if needs_hub:
        if hub is None:
            raise PolicyConfigError(
                f"policies[{index}]: {ptype!r} needs the hub, but none was provided to the loader")
        kwargs["hub"] = hub

    try:
        return cls(**kwargs)
    except TypeError as e:
        # Wrong/missing arguments: name the policy and the file position, because
        # the raw TypeError ("__init__() got an unexpected keyword argument") does
        # not tell an operator which entry of their file is wrong.
        raise PolicyConfigError(f"policies[{index}] ({ptype}): {e}") from e


def load_policies(path: str | Path, hub: "DoSyncHub | None" = None) -> list[BasePolicy]:
    """Build the policy objects declared in a deployment policy file.

    Raises PolicyConfigError on any problem — see the module docstring for why a
    policy file must never fail quietly.
    """
    path = Path(path)
    if not path.exists():
        raise PolicyConfigError(
            f"policy file not found: {path}. A policy file was configured but does not "
            "exist; refusing to start unprotected."
        )

    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise PolicyConfigError(f"{path}: invalid JSON — {e}") from e

    if not isinstance(doc, dict):
        raise PolicyConfigError(f"{path}: expected a JSON object at the top level")

    version = doc.get("version")
    if version != CONFIG_VERSION:
        raise PolicyConfigError(
            f"{path}: unsupported version {version!r} (this hub reads version {CONFIG_VERSION})")

    # Top-level "_"-prefixed keys (e.g. "_README") are metadata too.
    entries = doc.get("policies")
    if entries is None:
        raise PolicyConfigError(f"{path}: missing required field 'policies'")
    if not isinstance(entries, list):
        raise PolicyConfigError(f"{path}: 'policies' must be a list")

    policies = [_build_one(i, entry, hub) for i, entry in enumerate(entries)]
    log.info("Loaded %d deployment policy/policies from %s", len(policies), path)
    return policies


def load_into(engine, path: str | Path, hub: "DoSyncHub | None" = None) -> list[BasePolicy]:
    """Load a policy file and register every policy on the engine."""
    policies = load_policies(path, hub=hub)
    for p in policies:
        engine.add(p)
    return policies


def configured_path() -> str | None:
    """The deployment policy file, if this deployment configured one.

    None means "this deployment declares no policies", which is a legitimate and
    common state — not an error. The reference hub ships with NO deployment
    policies, because the protocol has no opinion about your house.
    """
    return os.environ.get("DOSYNC_POLICIES") or None
