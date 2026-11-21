"""Run the real rulebook against the fixtures, with stub playbooks that only record each call (skill eda-rulebook).

Offline: ansible-rulebook listens on loopback test ports, no real system is contacted. Needs ansible-rulebook and
Java (the dsgrow-eda container): skipped where ansible-rulebook is not installed (the Windows workstation).
"""
import hashlib
import hmac
import json
import os
import pathlib
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIX = ROOT / "tests" / "fixtures"
VCFOPS_TOKEN = "test-vcfops-token"
APPROVALS_TOKEN = "test-approvals-token"
HMAC_SECRET = "test-hmac-secret"
PORTS = {"vcfops": 5080, "approvals": 5081}

pytestmark = pytest.mark.skipif(shutil.which("ansible-rulebook") is None, reason="ansible-rulebook not installed")


def _wait_port(port, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise TimeoutError("port %d not listening" % port)


def _post(port, body, token, signature=None, path="endpoint"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/%s" % (port, path), data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    if signature is not None:
        req.add_header("x-dsgrow-signature", signature)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _sign(data):
    return "sha256=" + hmac.new(HMAC_SECRET.encode(), data, hashlib.sha256).hexdigest()


def _fixture(name):
    """'approvals/approve.json' as given; a bare name is a VCF Ops fixture (tests/fixtures/vcfops/)."""
    path = FIX / name if "/" in name else FIX / "vcfops" / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rulebook(tmp_path_factory):
    out = tmp_path_factory.mktemp("rulebook")
    calls = out / "calls.jsonl"
    env = dict(os.environ,
               DSGROW_EDA_VCFOPS_TOKEN=VCFOPS_TOKEN,
               DSGROW_EDA_APPROVALS_TOKEN=APPROVALS_TOKEN,
               DSGROW_APPROVAL_HMAC_SECRET=HMAC_SECRET,
               DSGROW_TEST_CALLS=str(calls))
    env.pop("ANSIBLE_CONFIG", None)   # the stubs need nothing from the repo's ansible.cfg
    # The rulebook's ports are fixed (5080/5081). With the live rulebook up on the host network the test would talk
    # to it (seen 2026-11-20: 401 with the test token): run in a network namespace of its own (docker --network none).
    for port in PORTS.values():
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                pytest.fail("port %d is already in use (the live rulebook?): run this test with docker run "
                            "--network none (CLAUDE.md Commands)" % port)
    log = open(out / "rulebook.log", "w")
    proc = subprocess.Popen(
        ["ansible-rulebook", "-r", str(ROOT / "rulebooks" / "datastore_capacity.yml"),
         "-i", str(HERE / "inventory.yml"), "--vars", str(ROOT / "rulebooks" / "vars.yml"),
         "--env-vars", "DSGROW_EDA_VCFOPS_TOKEN,DSGROW_EDA_APPROVALS_TOKEN,DSGROW_APPROVAL_HMAC_SECRET",
         "--print-events"],
        cwd=str(HERE), env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for port in PORTS.values():
            _wait_port(port)
        yield {"calls": calls, "log": out / "rulebook.log"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _calls(path, expect, timeout=120):
    """Wait until at least `expect` calls are recorded (playbooks run one at a time), then a little longer."""
    end = time.time() + timeout
    while time.time() < end:
        lines = path.read_text().splitlines() if path.exists() else []
        if len(lines) >= expect:
            time.sleep(5)   # anything unexpected would arrive now
            return [json.loads(x) for x in path.read_text().splitlines()]
        time.sleep(1)
    return [json.loads(x) for x in (path.read_text().splitlines() if path.exists() else [])]


def test_rulebook_routes_events(rulebook):
    vc = PORTS["vcfops"]
    ap = PORTS["approvals"]

    # authentication of the vcfops source
    assert _post(vc, _fixture("new_datastore_80.json"), "wrong-token") == 401

    # VCF Ops events, in order
    for name in ["new_datastore_80.json", "updated_datastore_85.json", "non_datastore_resource.json",
                 "unknown_schema.json", "other_alert_definition.json", "symptoms_na.json",
                 "approval_on_vcfops.json", "canceled_datastore.json", "new_datastore_again.json",
                 "updated_datastore_85.json", "no_alert_id.json"]:
        assert _post(vc, _fixture(name), VCFOPS_TOKEN) == 200, name
        time.sleep(1)

    # approvals: bad signature and wrong token are refused by the source; a signed one passes
    body = json.dumps(_fixture("approvals/approve.json")).encode()
    assert _post(ap, body, APPROVALS_TOKEN, signature="sha256=" + "0" * 64) == 401
    assert _post(ap, body, APPROVALS_TOKEN, signature=None) in (400, 401)
    assert _post(ap, body, VCFOPS_TOKEN, signature=_sign(body)) == 401
    assert _post(ap, body, APPROVALS_TOKEN, signature=_sign(body)) == 200

    calls = _calls(rulebook["calls"], expect=5)
    by = {}
    for c in calls:
        by.setdefault(c["playbook"], []).append(c)

    # propose, throttled per alert (owner 2026-11-21: every crossing of the limit gets a proposal): the new alert, the
    # N/A-symptoms alert (another datastore) and, after the cancel, a NEW alert on the same datastore; the "updated"
    # events of the first alert (before and after the cancel) are throttled; an event without alert_id is ignored
    got = sorted((c["resource_id"], c["alert_id"]) for c in by.get("propose", []))
    want = sorted((_fixture(n)["resource_id"], _fixture(n)["alert_id"])
                  for n in ["new_datastore_80.json", "symptoms_na.json", "new_datastore_again.json"])
    assert got == want, got
    assert all(c["source"] == "vcfops" for c in by["propose"])
    # cleared once
    assert [c["resource_id"] for c in by.get("cleared", [])] == [_fixture("canceled_datastore.json")["resource_id"]]
    # execute exactly once, and only from the approvals source (never the approval sent to vcfops)
    assert len(by.get("execute", [])) == 1
    assert by["execute"][0]["source"] == "approvals"
    assert by["execute"][0]["schema"] == "dsgrow.approval.v1"
    assert len(calls) == 5, calls
