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
import urllib.parse
import urllib.request

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIX = ROOT / "tests" / "fixtures"
VCFOPS_TOKEN = "test-vcfops-token"
APPROVALS_TOKEN = "test-approvals-token"
HMAC_SECRET = "test-hmac-secret"
PORTS = {"vcfops": 5080, "approvals": 5081}

# ansible renders event strings as Jinja templates when a playbook reads them, so a string like this one in ANY field of
# an event must keep every playbook from starting (the rulebook's allow-lists; tests/unit/test_rulebook_allowlist.py)
INJECTION = "{{ 7 * 7 }}"
ALERT_FIELDS = ["alert_id", "resource_id", "resource_name", "criticality", "cancel_time", "link", "adapter_instance"]
# fields no playbook reads: no pattern, so even template text in them stops nothing (it stays inert: tests/offline/test_flow.py)
UNREAD_FIELDS = ["status", "adapter_kind", "create_time", "update_time", "symptoms"]
APPROVAL_FIELDS = ["rid", "vc", "ds", "dev", "approver_id", "approver_name", "channel", "ts", "tgt"]
DENY_FIELDS = [f for f in APPROVAL_FIELDS if f != "tgt"]

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
               DSGROW_EDA_VCFOPS_BIND="0.0.0.0",
               DSGROW_TEST_CALLS=str(calls),
               # as in compose.yaml: the root filesystem is read-only, Ansible's scratch directories go elsewhere
               ANSIBLE_LOCAL_TEMP=str(out / "ansible-local"), ANSIBLE_REMOTE_TEMP=str(out / "ansible-remote"))
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
         "--env-vars",
         "DSGROW_EDA_VCFOPS_TOKEN,DSGROW_EDA_APPROVALS_TOKEN,DSGROW_APPROVAL_HMAC_SECRET,DSGROW_EDA_VCFOPS_BIND",
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

    # a valid optional adapter_instance passes (the allow-list's `is not defined or is match` branch)
    with_adapter = dict(_fixture("new_datastore_80.json"), alert_id="a1b2c3d4-0007-4000-8000-000000000007",
                        resource_id="4d4d4d4d-7777-4c3b-9a71-0d5f2e6b7a07", resource_name="ds-adapter07",
                        adapter_instance="vcenter01.example.lab")
    assert _post(vc, with_adapter, VCFOPS_TOKEN) == 200
    time.sleep(1)

    # template text in any field of an alert starts nothing, for propose and for cleared. Every event carries ids no
    # other event uses, so a wrongly accepted one could not hide behind the throttle.
    n = 0
    for event in ("new", "canceled"):
        for field in ALERT_FIELDS:
            n += 1
            hostile = dict(_fixture("new_datastore_80.json"), event=event,
                           alert_id="a1b2c3d4-0099-4000-8000-%012d" % n,
                           resource_id="7e7e7e7e-0099-4c3b-9a71-%012d" % n)
            hostile[field] = INJECTION
            assert _post(vc, hostile, VCFOPS_TOKEN) == 200, (event, field)
            time.sleep(0.3)
    # a real-looking alert with odd values in the text fields and template text only where nothing reads it IS proposed:
    # an allow-list that drops real alerts is worse than none
    odd = dict(_fixture("new_datastore_80.json"), alert_id="a1b2c3d4-0008-4000-8000-000000000008",
               resource_id="4d4d4d4d-8888-4c3b-9a71-0d5f2e6b7a08", resource_name="ds-odd08", criticality="${CRITICALITY}",
               cancel_time="${CANCEL_TIME}", link="N/A", sent_at=INJECTION, extra_key=INJECTION,
               **{f: INJECTION for f in UNREAD_FIELDS})
    assert _post(vc, odd, VCFOPS_TOKEN) == 200
    time.sleep(1)
    # a valid identifier followed by a newline: Python's re.match(...$) accepts it, so the ENGINE's matcher is tested too
    for n, field in enumerate(("alert_id", "resource_id", "resource_name"), 1):
        nl = dict(_fixture("new_datastore_80.json"), alert_id="a1b2c3d4-0097-4000-8000-%012d" % n,
                  resource_id="7e7e7e7e-0097-4c3b-9a71-%012d" % n)
        nl[field] = nl[field] + "\n"
        assert _post(vc, nl, VCFOPS_TOKEN) == 200, field
        time.sleep(0.3)
    # ... and in the URL path the sender chose (meta.endpoint travels in the event too)
    assert _post(vc, dict(_fixture("new_datastore_80.json"), alert_id="a1b2c3d4-0098-4000-8000-000000000098",
                          resource_id="7e7e7e7e-0098-4c3b-9a71-000000000098"),
                 VCFOPS_TOKEN, path=urllib.parse.quote(INJECTION, safe="")) == 200

    # approvals: template text in any field of an approval (token and signature are valid) starts no execute.yml
    approve = _fixture("approvals/approve.json")
    for field in APPROVAL_FIELDS[:-1]:
        body = json.dumps(dict(approve, **{field: INJECTION})).encode()
        assert _post(ap, body, APPROVALS_TOKEN, signature=_sign(body)) == 200, field
        time.sleep(0.3)
    # a name the bridge would have cleaned: not an approver_name the rulebook takes either
    body = json.dumps(dict(approve, approver_name="jane?doe")).encode()
    assert _post(ap, body, APPROVALS_TOKEN, signature=_sign(body)) == 200
    # ... the same for a denial, which has its own rule and its own playbook
    deny = _fixture("approvals/deny.json")
    for field in DENY_FIELDS:
        body = json.dumps(dict(deny, **{field: INJECTION})).encode()
        assert _post(ap, body, APPROVALS_TOKEN, signature=_sign(body)) == 200, field
        time.sleep(0.3)
    # last: a non-numeric target (how the engine compares text with a number is the one thing no test has seen)
    body = json.dumps(dict(approve, tgt=INJECTION)).encode()
    assert _post(ap, body, APPROVALS_TOKEN, signature=_sign(body)) == 200
    time.sleep(1)

    # approvals: bad signature and wrong token are refused by the source; a signed one passes
    body = json.dumps(_fixture("approvals/approve.json")).encode()
    assert _post(ap, body, APPROVALS_TOKEN, signature="sha256=" + "0" * 64) == 401
    assert _post(ap, body, APPROVALS_TOKEN, signature=None) in (400, 401)
    assert _post(ap, body, VCFOPS_TOKEN, signature=_sign(body)) == 401
    assert _post(ap, body, APPROVALS_TOKEN, signature=_sign(body)) == 200
    # a signed denial starts deny.yml, never execute.yml; an approve decision never starts deny.yml
    body = json.dumps(_fixture("approvals/deny.json")).encode()
    assert _post(ap, body, APPROVALS_TOKEN, signature=_sign(body)) == 200
    body = json.dumps(_fixture("approvals/deny.json")).encode()
    assert _post(ap, body, VCFOPS_TOKEN, signature=_sign(body)) == 401
    assert _post(vc, _fixture("approvals/deny.json"), VCFOPS_TOKEN) == 200   # a denial sent to vcfops: ignored

    calls = _calls(rulebook["calls"], expect=8)
    # first of all: an event with template text in a field must not have started anything (before this check the
    # stubs of an unprotected rulebook recorded 49 for "{{ 7 * 7 }}": the engine rendered it)
    rendered = [c for c in calls if any(not isinstance(v, str) for v in c.values())
                or any("{{" in str(v) or str(v) == "49" for v in c.values())]
    assert not rendered, "template text in an event reached a playbook and was rendered: %s" % rendered
    assert len(calls) == 8, "an event with template text started a playbook (%d calls): %s" % (len(calls), calls)
    # ... and the log says so for each of them (a dropped alert is never silent): 2 x 12 fields, the path, no_alert_id
    time.sleep(2)
    log_text = pathlib.Path(rulebook["log"]).read_text(encoding="utf-8", errors="replace")
    assert log_text.count("dsgrow REJECTED a vcfops event") >= 2 * len(ALERT_FIELDS), log_text.count("REJECTED")
    by = {}
    for c in calls:
        by.setdefault(c["playbook"], []).append(c)

    # propose, throttled per alert (owner 2026-11-21: every crossing of the limit gets a proposal): the new alert, the
    # N/A-symptoms alert (another datastore), after the cancel a NEW alert on the same datastore and one that names
    # its vCenter; the "updated" events of the first alert (before and after the cancel) are throttled; an event
    # without alert_id is ignored; every event with template text in a field is ignored (no call at all)
    got = sorted((c["resource_id"], c["alert_id"]) for c in by.get("propose", []))
    want = sorted([(_fixture(n)["resource_id"], _fixture(n)["alert_id"])
                   for n in ["new_datastore_80.json", "symptoms_na.json", "new_datastore_again.json"]]
                  + [(with_adapter["resource_id"], with_adapter["alert_id"]), (odd["resource_id"], odd["alert_id"])])
    assert got == want, got
    assert all(c["source"] == "vcfops" for c in by["propose"])
    # cleared once
    assert [c["resource_id"] for c in by.get("cleared", [])] == [_fixture("canceled_datastore.json")["resource_id"]]
    # execute exactly once, and only from the approvals source (never the approval sent to vcfops)
    assert len(by.get("execute", [])) == 1
    assert by["execute"][0]["source"] == "approvals"
    assert by["execute"][0]["schema"] == "dsgrow.approval.v1"
    # deny exactly once, from the approvals source only (never the denial sent to vcfops)
    assert len(by.get("deny", [])) == 1
    assert by["deny"][0]["source"] == "approvals" and by["deny"][0]["resource_id"] == _fixture("approvals/deny.json")["rid"]
    # 5 propose + 1 cleared + 1 execute + 1 deny: nothing for any event with template text in a field a playbook reads
    assert len(calls) == 8, calls
