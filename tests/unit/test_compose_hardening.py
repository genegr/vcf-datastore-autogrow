"""The two long-running services are the network-facing part: the rulebook listens on 0.0.0.0:5080, the bridge holds the
Slack tokens. What they may see and do is fixed here (compose.yaml), and tools/offline.sh runs the real rulebook and the
real playbooks under exactly those settings (checkout read-only, .state hidden, no capability), so a change that needs
more fails there, offline, before it reaches the service. Skipped where PyYAML is missing.
"""
import pathlib

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _services():
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))["services"]


def test_the_rulebook_sees_the_code_read_only_and_writes_only_state():
    rulebook = _services()["rulebook"]
    assert rulebook["volumes"] == ["./:/work:ro", "./state:/work/state"]
    assert "/work/.state" in rulebook["tmpfs"], ".state (vault password, env files) must be hidden behind a tmpfs"
    assert rulebook["environment"]["DSGROW_STATE_DIR"] == "/work/state"


def test_the_rulebook_has_no_home_under_the_secrets_directory():
    home = _services()["rulebook"]["environment"]["HOME"]
    assert not home.startswith("/work/.state"), home


@pytest.mark.parametrize("name", ["rulebook", "bridge"])
def test_the_network_facing_services_drop_every_capability(name):
    service = _services()[name]
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]


def test_the_bridge_mounts_nothing():
    assert "volumes" not in _services()["bridge"], "its code is baked into the image"


def test_no_service_but_the_tools_one_mounts_the_checkout_writable():
    for name, service in _services().items():
        for mount in service.get("volumes", []):
            if mount.startswith("./:") or mount.startswith(".:"):
                assert name == "eda" or mount.endswith(":ro"), "%s mounts the checkout writable: %s" % (name, mount)


def test_offline_checks_run_under_the_services_own_settings():
    """The claim above: the rulebook test and the flow tests use the flags compose.yaml gives the rulebook service."""
    for script in ("offline.sh", "flow_tests.sh"):
        text = (ROOT / "tools" / script).read_text(encoding="utf-8")
        for flag in ("--network none", "--cap-drop ALL", "--security-opt no-new-privileges:true", "/work:ro"):
            assert flag in text, "tools/%s lacks %s" % (script, flag)
    offline = (ROOT / "tools" / "offline.sh").read_text(encoding="utf-8")
    assert "--tmpfs /work/.state" in offline and "tests/rulebook" in offline and "tools/flow_tests.sh" in offline
    assert "--tmpfs /work/.state" in (ROOT / "tools" / "flow_tests.sh").read_text(encoding="utf-8")


def test_the_tools_service_still_has_the_whole_tree():
    """setup/ and probes write .state (secrets, logs, home): the one service that is not network facing."""
    assert "./:/work" in _services()["eda"]["volumes"]
