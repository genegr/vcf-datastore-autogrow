"""setup/vcfops.yml only CREATES the webhook outbound instance. The task "An existing outbound instance points where the
inventory says" must make a changed host, port or scheme visible instead of a silent changed=0 (a TLS setting that does
nothing is worse than none). The REAL task is taken out of the playbook and run alone by ansible-playbook against fake
VCF Operations answers: no network. Skipped without ansible-playbook or PyYAML (the Windows workstation).
"""
import json
import pathlib
import shutil
import subprocess

import pytest

yaml = pytest.importorskip("yaml")
ROOT = pathlib.Path(__file__).resolve().parents[2]
TASK = "An existing outbound instance points where the inventory says"
pytestmark = pytest.mark.skipif(shutil.which("ansible-playbook") is None, reason="ansible-playbook not installed")


def _find(node, name):
    if isinstance(node, list):
        for x in node:
            hit = _find(x, name)
            if hit:
                return hit
    elif isinstance(node, dict):
        if node.get("name") == name:
            return node
        for key in ("block", "rescue", "always", "tasks"):
            hit = _find(node.get(key), name)
            if hit:
                return hit
    return None


def run(tmp_path, instances, scheme=None):
    task = _find(yaml.safe_load((ROOT / "setup" / "vcfops.yml").read_text(encoding="utf-8")), TASK)
    assert task, "the task is gone from setup/vcfops.yml"
    variables = {"vo_plugin": "dsgrow-eda", "dsgrow_eda_host": "run-host.example.lab", "dsgrow_eda_vcfops_port": 5080,
                 "vo_pl": {"json": {"notificationPluginInstances": instances}}}
    if scheme:
        variables["dsgrow_eda_vcfops_scheme"] = scheme
    play = [{"hosts": "localhost", "gather_facts": False, "vars": variables, "tasks": [task]}]
    (tmp_path / "play.yml").write_text(json.dumps(play), encoding="utf-8")
    env = {"ANSIBLE_NOCOLOR": "1", "HOME": str(tmp_path), "ANSIBLE_LOCAL_TEMP": str(tmp_path / "t"),
           "PATH": __import__("os").environ["PATH"]}
    return subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(tmp_path / "play.yml")],
                          capture_output=True, text=True, env=env, timeout=300, cwd=str(tmp_path))


def instance(url, name="dsgrow-eda"):
    return {"name": name, "pluginId": "x", "configValues": [{"name": "Url", "value": url},
                                                           {"name": "ConnectionTimeoutInSeconds", "value": "30"}]}


@pytest.mark.parametrize("have,scheme", [
    ("http://run-host.example.lab:5080", None), ("http://run-host.example.lab:5080/", None),
    ("HTTP://Run-Host.example.lab:5080", None), ("https://run-host.example.lab:5080", "https"),
])
def test_an_instance_that_matches_the_inventory_passes(tmp_path, have, scheme):
    proc = run(tmp_path, [instance(have)], scheme)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("have,scheme", [
    ("http://run-host.example.lab:5080", "https"),           # the TLS setting that would otherwise do nothing
    ("https://run-host.example.lab:5080", None),
    ("http://other-host.example.lab:5080", None), ("http://run-host.example.lab:5000", None), ("", None),
])
def test_an_instance_that_differs_is_refused_with_both_values(tmp_path, have, scheme):
    proc = run(tmp_path, [instance(have)], scheme)
    text = proc.stdout + proc.stderr
    assert proc.returncode != 0 and "already exists with Url" in text and "run-host.example.lab:5080" in text, text


def test_no_instance_means_the_create_step_decides(tmp_path):
    proc = run(tmp_path, [instance("http://elsewhere:1", name="another-instance")])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skipping" in proc.stdout or "skipped=1" in proc.stdout


def test_an_instance_without_a_url_is_a_mismatch(tmp_path):
    bare = {"name": "dsgrow-eda", "pluginId": "x", "configValues": []}
    assert run(tmp_path, [bare]).returncode != 0
