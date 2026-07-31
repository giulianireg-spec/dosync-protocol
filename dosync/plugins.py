"""Third-party adapters, discovered through Python entry points.

The third of the three ways a technology reaches a deployment, and the one that
lets someone other than this project answer for a device. A vendor publishes
`dosync-adapter-daikin` to PyPI, an operator installs it deliberately, and the
hub finds it — without a pull request here, and without this project promising
to maintain code for hardware it has never seen.

**Why this and not a plugin repository.** DESIGN-PRINCIPLES rules out fetching
adapter code from a remote source, because the entire argument of this protocol
is that nothing actuates a device without passing a policy and leaving a record,
and downloading executable code whose purpose is to move hardware would put the
largest possible hole exactly where the guarantee lives. An entry point is
different in the two ways that matter: **someone chose to install it**, through a
package manager with a supply chain behind it, and **someone's name is on it**.

That difference is not decoration. A third-party adapter is code running inside
the hub with the hub's permissions — it can do anything the hub can. The hub
therefore says loudly which ones it loaded and where they came from, and records
their arrival in the audit chain, because "what code was running when this
happened" is a question an incident review will ask.

Publishing one:

    [project.entry-points."dosync.adapters"]
    daikin = "dosync_adapter_daikin:DaikinAdapter"

The object named must be a `DoSyncAdapter` subclass. It is instantiated with
`hub=` if its constructor accepts it.
"""
import inspect
import logging

# Imported at module level rather than inside the function so a test can replace
# it. A dependency reached for at call time is one a test cannot substitute, and
# the first version of this module could only be tested against whatever
# happened to be installed in the environment running the suite.
try:
    from importlib.metadata import entry_points
except ImportError:      # pragma: no cover - Python < 3.8
    entry_points = None

log = logging.getLogger("dosync.plugins")

#: The entry point group third-party adapters advertise.
ENTRY_POINT_GROUP = "dosync.adapters"


def discover_third_party_adapters(hub=None) -> list:
    """Instantiate every adapter advertised by an installed package.

    Returns `(name, adapter, distribution)` tuples. A plugin that fails to load
    is logged and skipped: a broken third-party package must not stop a hub from
    starting, or one vendor's bad release takes a building offline.
    """
    if entry_points is None:      # pragma: no cover - Python < 3.8
        return []

    try:
        found = entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:        # pragma: no cover - older selection API
        found = entry_points().get(ENTRY_POINT_GROUP, [])
    except Exception as e:   # pragma: no cover - defensive
        log.warning("Could not read entry points: %s", e)
        return []

    from .adapters import DoSyncAdapter

    loaded = []
    for ep in found:
        origin = getattr(getattr(ep, "dist", None), "name", None) or "unknown package"
        try:
            obj = ep.load()
        except Exception as e:
            log.error("Third-party adapter '%s' from %s failed to import: %s — "
                      "skipped. The hub is running without it.", ep.name, origin, e)
            continue

        if not (inspect.isclass(obj) and issubclass(obj, DoSyncAdapter)):
            log.error("Third-party adapter '%s' from %s is not a DoSyncAdapter "
                      "subclass (got %r) — skipped.", ep.name, origin, obj)
            continue

        try:
            # Pass the hub only if the constructor wants it; a plugin author
            # should not be forced into a signature to be loadable.
            params = inspect.signature(obj.__init__).parameters
            adapter = obj(hub=hub) if "hub" in params else obj()
        except Exception as e:
            log.error("Third-party adapter '%s' from %s failed to initialise: %s "
                      "— skipped.", ep.name, origin, e)
            continue

        # Declared for what it is. A plugin cannot claim to be an ecosystem
        # adapter of this project by setting a class attribute — where the code
        # came from is not the plugin's to assert.
        try:
            adapter.adapter_kind = "third_party"
        except Exception:    # pragma: no cover - exotic descriptors
            pass

        loaded.append((ep.name, adapter, origin))
        log.warning(
            "Third-party adapter loaded: '%s' from %s. It runs inside this hub "
            "with the hub's permissions.", ep.name, origin)
    return loaded
