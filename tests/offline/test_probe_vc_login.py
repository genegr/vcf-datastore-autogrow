"""tests/probes/vc_login.yml prints the vCenter's product, version and build next to "login ok". The first version asked for
a key (`product_version`) the module does not return, so it printed "?". The REAL `Result` task of the probe is run alone
against the sample that community.vmware.vmware_about_info documents for itself (read from `ansible-doc` in the image):
no network, no vCenter. Skipped without ansible-playbook / ansible-doc or PyYAML (the Windows workstation).
"""
import json
import os
import pathlib
import shutil
import subprocess

import pytest

yaml = pytest.importorskip("yaml")
ROOT = pathlib.Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("ansible-playbook") is None or shutil.which("ansible-doc") is None,
                                reason="ansible is not installed")


def _documented_sample():
    doc = subprocess.run(["ansible-doc", "-t", "module", "--json", "community.vmware.vmware_about_info"],
                         capture_output=True, text=True, timeout=120, check=True)
    return json.loads(doc.stdout)["community.vmware.vmware_about_info"]["return"]["about_info"]["sample"]


def _result_task():
    play = yaml.safe_load((ROOT / "tests" / "probes" / "vc_login.yml").read_text(encoding="utf-8"))[0]
    return next(t for t in play["tasks"] if t.get("name") == "Result")


def test_the_probe_prints_the_version_the_module_documents(tmp_path):
    sample = _documented_sample()
    assert {"product_name", "version", "build"} <= set(sample), "the module no longer documents these keys"
    results = [{"dsgrow_vc": {"hostname": "vc01.example.lab", "username": "dsgrow@sso.example"}, "about_info": sample}]
    play = [{"hosts": "localhost", "gather_facts": False, "vars": {"vl_about": {"results": results}},
             "tasks": [_result_task()]}]
    (tmp_path / "play.yml").write_text(json.dumps(play), encoding="utf-8")
    env = {"ANSIBLE_NOCOLOR": "1", "HOME": str(tmp_path), "ANSIBLE_LOCAL_TEMP": str(tmp_path / "t"), "PATH": os.environ["PATH"]}
    proc = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(tmp_path / "play.yml")],
                          capture_output=True, text=True, env=env, timeout=300, cwd=str(tmp_path))
    text = proc.stdout + proc.stderr
    assert proc.returncode == 0, text
    assert sample["product_name"] in text and sample["version"] in text and "build %s" % sample["build"] in text, text
    assert "?" not in text.split("login ok")[0].split("vc01.example.lab:")[-1], "a key was not found: %s" % text
