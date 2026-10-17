"""The image and the Home Assistant add-on start the hub the same way (D1, D2).

`dosync-container` keeps every write under the data volume, turns add-on options
into the hub's settings without overriding what the person set explicitly, and
connects the Home Assistant bridge through the Supervisor's token. The add-on's
manifest is checked here for what the Supervisor needs and for agreeing with the
package: same version, options its schema declares, the published image.
"""
import json
import re
from pathlib import Path

import yaml

import dosync
from dosync.container import SUPERVISOR_CORE, settings

REPO = Path(__file__).resolve().parent.parent
ADDON = REPO / "homeassistant-addon"


def test_plain_docker_keeps_everything_under_the_volume(tmp_path):
    env, notes = settings({}, tmp_path)
    assert env["DOSYNC_DB"] == str(tmp_path / "dosync.db")
    assert env["DOSYNC_CERTS_DIR"] == str(tmp_path / "certs")
    assert env["XDG_STATE_HOME"].startswith(str(tmp_path))
    assert env["DOSYNC_INSTALL_MODE"] == "user", "root in a container is not a system install"
    assert "DOSYNC_AUTH" not in env, "authentication stays on (the hub's default)"
    assert "DOSYNC_POLICIES" not in env and not notes


def test_a_policy_file_in_the_volume_is_used(tmp_path):
    (tmp_path / "policies.json").write_text('{"version": 1, "policies": []}')
    env, _ = settings({}, tmp_path)
    assert env["DOSYNC_POLICIES"] == str(tmp_path / "policies.json")


def test_addon_options_become_settings_and_the_bridge_uses_the_supervisor(tmp_path):
    (tmp_path / "options.json").write_text(json.dumps({
        "auth_required": True, "operator_token": "the-operators-own",
        "configuration_credential": "operator", "home_assistant_bridge": True,
        "confirmation_timeout": 600, "log_level": "debug"}))
    env, notes = settings({"SUPERVISOR_TOKEN": "sv-token"}, tmp_path)
    assert env["DOSYNC_AUTH"] == "true"
    assert env["DOSYNC_OPERATOR_TOKEN"] == "the-operators-own"
    assert env["DOSYNC_CONFIGURATION_CREDENTIAL"] == "operator"
    assert env["DOSYNC_CONFIRMATION_TIMEOUT"] == "600"
    assert env["HA_URL"] == SUPERVISOR_CORE and env["HA_TOKEN"] == "sv-token"
    assert any("add-on" in n for n in notes)


def test_explicit_environment_wins_over_options(tmp_path):
    (tmp_path / "options.json").write_text(json.dumps({
        "auth_required": False, "home_assistant_bridge": False, "operator_token": ""}))
    env, _ = settings({"DOSYNC_AUTH": "true", "SUPERVISOR_TOKEN": "x"}, tmp_path)
    assert env["DOSYNC_AUTH"] == "true"
    assert "HA_URL" not in env, "bridge off means no bridge"
    assert "DOSYNC_OPERATOR_TOKEN" not in env, "an empty option sets nothing"


def test_the_addon_manifest_agrees_with_the_package():
    cfg = yaml.safe_load((ADDON / "config.yaml").read_text())
    for key in ("name", "version", "slug", "description", "arch", "image", "options", "schema"):
        assert key in cfg, key
    assert cfg["version"] == dosync.__version__, "add-on and package versions must move together"
    assert cfg["image"] == "ghcr.io/giulianireg-spec/dosync-protocol"
    assert set(cfg["arch"]) <= {"aarch64", "amd64", "armv7", "armhf", "i386"}
    assert set(cfg["options"]) <= set(cfg["schema"]), "every option needs a schema entry"
    from dosync.container import _OPTIONS
    handled = set(_OPTIONS) | {"home_assistant_bridge", "log_level"}
    assert set(cfg["schema"]) <= handled, "an option the container ignores would do nothing"
    tr = yaml.safe_load((ADDON / "translations" / "en.yaml").read_text())
    assert set(cfg["schema"]) <= set(tr["configuration"])
    repo = yaml.safe_load((REPO / "repository.yaml").read_text())
    assert repo["name"] and repo["url"].startswith("https://")


def test_the_image_starts_through_the_container_entry_point():
    dockerfile = (REPO / "Dockerfile").read_text()
    assert re.search(r'^CMD \["dosync-container"\]', dockerfile, re.M)
    assert "[mcp]" in dockerfile
    assert "DOSYNC_AUTH=false" not in re.sub(r"#.*", "", dockerfile), "auth must stay on in the image"


def test_the_dashboard_works_under_a_path_prefix_and_the_addon_declares_ingress():
    """Home Assistant's ingress serves the dashboard at /api/hassio_ingress/<token>/.
    The page used to address the root of the host, so every call missed the hub.
    (Checked end to end through a prefix proxy in a browser on 2026-10-17.)"""
    import yaml
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    html = (root / "dosync" / "dashboard.html").read_text(encoding="utf-8")
    assert "const BASE = window.location.pathname" in html
    assert "${window.location.host}${BASE}" in html
    cfg = yaml.safe_load((root / "homeassistant-addon" / "config.yaml").read_text())
    assert cfg["ingress"] is True and cfg["ingress_port"] == 47200


def test_every_file_in_the_repository_can_be_checked_out_on_windows():
    """A cover image named with a '?' stopped the Windows CI job at checkout."""
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    try:
        names = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                               text=True, check=True).stdout.splitlines()
    except Exception:
        import pytest
        pytest.skip("not a git checkout")
    bad = [n for n in names if any(c in n for c in '<>:"|?*\\')]
    assert not bad, bad
