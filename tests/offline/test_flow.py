"""The REAL playbooks (propose.yml, execute.yml, deny.yml, cleared.yml) run offline against a fake world.

No real system is contacted (docker --network none): the FlashArray and vSphere modules are the stubs under
tests/offline/stubs (a JSON "world" file that every stub reads, changes and logs its calls in) and Slack is a fake HTTP
server on loopback. What is real: the playbooks, the roles, the filters, the state files, the Jinja. So this is where
the safety rules of the execute path are tested (CLAUDE.md rules 1-6, 9, 11) and the request lifecycle: a denied or
finished request, a second approval, a Slack error after the work was done.

Needs ansible-playbook: skipped where it is not installed (the Windows workstation); tools/offline.sh and
`docker run --network none ... dsgrow-eda:1 python -m pytest tests/offline` run it.
"""
from __future__ import annotations

import copy
import http.server
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import threading
import time
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
STUBS = pathlib.Path(__file__).resolve().parent / "stubs"
FIX = ROOT / "tests" / "fixtures"
GIB = 1024 ** 3
DEVICE = "eui.00361019ecace43d24a93783000120a4"
OTHER_DEVICE = "eui.00aaaaaaaaaaaaaa24a937bbbbbbbbbb"
VC, DS = "vcenter01.example.lab", "ds-capacity01"
ARRAY = "array-a.example.lab"
APPROVER = "U0DEMO0001"
CHANNEL, TS = "C0TEST0001", "1763546400.000100"

pytestmark = pytest.mark.skipif(shutil.which("ansible-playbook") is None, reason="ansible-playbook not installed")


def _serial(device):
    spec = importlib.util.spec_from_file_location("dsgrow_filter_under_test", ROOT / "plugins" / "filter" / "dsgrow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.dsgrow_device_to_fa_serial(device)


class FakeSlack:
    """Slack's Web API on loopback: records every call; `fail_when(method, body)` makes a call answer ok:false."""

    def __init__(self):
        self.requests, self.fail_when, self._n = [], None, 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method = self.path.rsplit("/", 1)[-1]
                outer.requests.append((method, body))
                outer._n += 1
                if outer.fail_when and outer.fail_when(method, body):
                    answer = {"ok": False, "error": "ratelimited"}
                else:
                    answer = {"ok": True, "channel": body.get("channel"), "ts": "1763546400.%06d" % outer._n}
                data = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def texts(self, method=None):
        return [json.dumps(b) for m, b in self.requests if method in (None, m)]


class Lab:
    """One test's fake world, state directory and Slack, plus helpers to run the real playbooks against them."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.state = tmp / "state"
        self.world_path = tmp / "world.json"
        self.slack = FakeSlack()
        self.world_path.write_text(json.dumps(self.new_world()), encoding="utf-8")
        for sub in ("requests", "locks"):
            (self.state / sub).mkdir(parents=True)

    # ---------------------------------------------------------------- the world
    @staticmethod
    def new_world(volume=200 * GIB, vmfs=199 * GIB, used=170 * GIB, hosts=("esx01.example.lab", "esx02.example.lab")):
        return {
            "calls": [],
            "vc": {"datastore": DS, "device": DEVICE, "used": used, "vmfs_capacity": vmfs, "array": ARRAY,
                   "volume": DS,
                   "hosts": [{"name": h, "mounted": True, "accessible": True, "device_size": volume,
                              "vmfs_capacity": vmfs} for h in hosts]},
            "arrays": {ARRAY: {"capacity": {"total_capacity": 29509027889152, "total_used": 10000000000000,
                                            "free_space": 19509027889152},
                               "volumes": {DS: {"serial": _serial(DEVICE), "size": volume}}}},
        }

    def world(self):
        return json.loads(self.world_path.read_text(encoding="utf-8"))

    def edit_world(self, change):
        world = self.world()
        change(world)
        self.world_path.write_text(json.dumps(world), encoding="utf-8")

    def calls(self, module=None):
        return [c for c in self.world()["calls"] if module in (None, c["module"])]

    def volume_bytes(self):
        return self.world()["arrays"][ARRAY]["volumes"][DS]["size"]

    # ---------------------------------------------------------------- the state files
    def lock_path(self):
        return self.state / "locks" / ("%s__%s.json" % (VC, DS))

    def request(self, rid):
        return json.loads((self.state / "requests" / ("%s.json" % rid)).read_text(encoding="utf-8"))

    def request_bytes(self, rid):
        return (self.state / "requests" / ("%s.json" % rid)).read_bytes()

    def seed(self, status="proposed", expires_in=4 * 3600, rid=None, lock=True, **fields):
        """A request file and lock as propose.yml leaves them (without running it)."""
        rid = rid or str(uuid.uuid4())
        now = int(time.time())
        request = {"rid": rid, "status": status, "created_at": "2026-11-23T10:00:00+00:00", "expires": now + expires_in,
                   "alert": {"alert_id": "a1b2c3d4-0001-4000-8000-000000000001"}, "vc": VC, "vc_key": "vc01",
                   "ds": DS, "dev": DEVICE, "fa_array": "array-a", "fa_volume": DS,
                   "at_proposal": {"volume_bytes": 200 * GIB, "vmfs_capacity_bytes": 199 * GIB},
                   "options": [272 * GIB], "refused": [], "slack": {"channel": CHANNEL, "ts": TS},
                   "blocks": [{"type": "header", "text": {"type": "plain_text", "text": "Expand"}},
                              {"type": "actions", "block_id": "dsgrow_actions", "elements": []}]}
        request.update(fields)
        (self.state / "requests" / ("%s.json" % rid)).write_text(json.dumps(request), encoding="utf-8")
        if lock:
            self.lock_path().write_text(json.dumps({"rid": rid, "status": status if status in
                                                    ("proposed", "executing") else "proposed",
                                                    "expires": now + expires_in}), encoding="utf-8")
        return rid

    # ---------------------------------------------------------------- running the real playbooks
    def run(self, playbook, event, extra=None):
        extra_vars = {
            "ansible_eda": {"event": event, "rule": "test", "ruleset": "test"},
            "dsgrow_slack_api": self.slack.url,
            "dsgrow_size_step_bytes": 16 * GIB,
            "dsgrow_vcenters": [{"key": "vc01", "hostname": VC, "datacenter": "dc01",
                                 "username": "dsgrow@vsphere.local", "password_env": "DSGROW_VC_PASSWORD_VC01",
                                 "validate_certs": True, "adapter_instances": [VC]}],
            "dsgrow_flasharrays": [{"name": "array-a", "key": "fa-a", "url": "https://%s" % ARRAY,
                                    "token_env": "DSGROW_FA_TOKEN_FA_A"}],
        }
        extra_vars.update(extra or {})
        (self.tmp / "extra.json").write_text(json.dumps(extra_vars), encoding="utf-8")
        env = dict(
            os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"), ANSIBLE_LIBRARY=str(STUBS / "library"),
            ANSIBLE_COLLECTIONS_PATH="%s:/usr/share/ansible/collections" % (STUBS / "collections"),
            ANSIBLE_NOCOLOR="1", ANSIBLE_LOCAL_TEMP=str(self.tmp / "ans-local"),
            ANSIBLE_REMOTE_TEMP=str(self.tmp / "ans-remote"), HOME=str(self.tmp),
            DSGROW_FAKE_WORLD=str(self.world_path), DSGROW_STATE_DIR=str(self.state), SLACK_BOT_TOKEN="xoxb-test",
            DSGROW_SLACK_CHANNEL=CHANNEL, DSGROW_APPROVER_IDS="%s, U0DEMO0002" % APPROVER,
            DSGROW_VC_PASSWORD_VC01="not-a-secret", DSGROW_FA_TOKEN_FA_A="not-a-secret")
        return subprocess.run(
            ["ansible-playbook", "-i", "localhost,", "-c", "local", "-e", "@%s" % (self.tmp / "extra.json"),
             str(ROOT / "playbooks" / playbook)],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)

    @staticmethod
    def event(source, payload, uuid_=None):
        return {"payload": payload, "meta": {"source": {"name": source, "type": "ansible.eda.webhook"},
                                             "uuid": uuid_ or str(uuid.uuid4()),
                                             "received_at": "2026-11-23T10:00:00.000000Z",
                                             "endpoint": "endpoint", "headers": {}}}

    def propose(self, alert="new_datastore_80.json", **over):
        payload = json.loads((FIX / "vcfops" / alert).read_text(encoding="utf-8"))
        payload.update(over)
        rid = str(uuid.uuid4())
        return rid, self.run("propose.yml", self.event("vcfops", payload, rid))

    def approve(self, rid, target, **over):
        payload = json.loads((FIX / "approvals" / "approve.json").read_text(encoding="utf-8"))
        payload.update(rid=rid, tgt=target, channel=CHANNEL, ts=TS, **over)
        return self.run("execute.yml", self.event("approvals", payload))

    def deny(self, rid, **over):
        payload = json.loads((FIX / "approvals" / "deny.json").read_text(encoding="utf-8"))
        payload.update(rid=rid, channel=CHANNEL, ts=TS, **over)
        return self.run("deny.yml", self.event("approvals", payload))

    def cancel(self, **over):
        payload = json.loads((FIX / "vcfops" / "canceled_datastore.json").read_text(encoding="utf-8"))
        payload.update(over)
        return self.run("cleared.yml", self.event("vcfops", payload))

    def close(self):
        self.slack.close()


@pytest.fixture
def lab(tmp_path):
    lab = Lab(tmp_path)
    yield lab
    lab.close()


def out(proc):
    return proc.stdout + proc.stderr


def writes(lab):
    """The calls that changed anything on the array or the hosts."""
    return [c for c in lab.calls() if (c["module"] == "purefa_volume" and c["changed"])
            or c["module"] in ("vmware_host_scanhba", "vmware_host_datastore")]


# ====================================================================== the happy path, from the alert to the datastore
def test_alert_to_expanded_datastore(lab):
    rid, proposed = lab.propose()
    assert proposed.returncode == 0, out(proposed)
    request = lab.request(rid)
    assert request["status"] == "proposed" and request["ds"] == DS and request["dev"] == DEVICE
    assert request["options"] == [256 * GIB, 272 * GIB, 288 * GIB]
    assert json.loads(lab.lock_path().read_text())["rid"] == rid
    assert writes(lab) == [], "proposing is read only"

    done = lab.approve(rid, 272 * GIB)
    assert done.returncode == 0, out(done)
    assert lab.volume_bytes() == 272 * GIB
    world = lab.world()
    assert world["vc"]["vmfs_capacity"] == 272 * GIB
    assert {h["vmfs_capacity"] for h in world["vc"]["hosts"]} == {272 * GIB}
    request = lab.request(rid)
    assert request["status"] == "done"
    assert request["approval"]["approver_id"] == APPROVER and request["approval"]["approver_name"] == "demo-approver"
    assert request["approval"]["target_bytes"] == 272 * GIB
    assert request["result"]["volume_after_bytes"] == 272 * GIB and request["result"]["fa_extended"] is True
    assert json.loads(lab.lock_path().read_text())["status"] == "released"
    assert len([c for c in lab.calls("purefa_volume") if c["changed"]]) == 1, "one write to the array, ever"
    assert [c for c in lab.calls("purefa_volume")][0]["created"] is False
    thread = " ".join(lab.slack.texts("chat.postMessage"))
    assert "Expanded" in thread and "<@%s>" % APPROVER in thread


# ====================================================================== rules 1-6: what execute refuses, before it writes
def refused(lab, proc, rid, needle, status="proposed"):
    assert proc.returncode != 0, "the approval was accepted"
    assert needle in out(proc), out(proc)
    assert writes(lab) == [], "a refusal must not write anything: %s" % writes(lab)
    assert lab.volume_bytes() == 200 * GIB
    assert lab.request(rid)["status"] == status


def test_refuses_an_approver_who_is_not_listed(lab):
    rid = lab.seed()
    refused(lab, lab.approve(rid, 272 * GIB, approver_id="U0INTRUDER"), rid, "U0INTRUDER is not an approver")


def test_refuses_an_unknown_request(lab):
    rid = str(uuid.uuid4())
    proc = lab.approve(rid, 272 * GIB)
    assert proc.returncode != 0 and "is unknown: no proposal wrote it" in out(proc)
    assert writes(lab) == []


@pytest.mark.parametrize("field,value", [("vc", "vcenter99.example.lab"), ("ds", "ds-other"),
                                         ("dev", OTHER_DEVICE)])
def test_refuses_identifiers_that_differ_from_the_request(lab, field, value):
    rid = lab.seed()
    refused(lab, lab.approve(rid, 272 * GIB, **{field: value}), rid, "identifiers differ from the request")


def test_refuses_an_expired_request(lab):
    rid = lab.seed(expires_in=-60)
    refused(lab, lab.approve(rid, 272 * GIB), rid, "request expired")


def test_refuses_while_another_request_holds_the_datastore(lab):
    rid = lab.seed()
    lab.lock_path().write_text(json.dumps({"rid": str(uuid.uuid4()), "status": "executing",
                                           "expires": int(time.time()) + 3600}), encoding="utf-8")
    refused(lab, lab.approve(rid, 272 * GIB), rid, "is locked by")


@pytest.mark.parametrize("status", ["cleared", "denied", "expired"])
def test_refuses_a_request_that_is_not_open(lab, status):
    rid = lab.seed(status=status)
    refused(lab, lab.approve(rid, 272 * GIB), rid, "request is %s" % status, status=status)


def test_refuses_a_target_over_the_caps(lab):
    """Rule 3: +50 % per request (200 GiB -> 304 GiB is the most); the human's size is never clamped."""
    rid = lab.seed()
    refused(lab, lab.approve(rid, 320 * GIB), rid, "more than 50%", status="failed")


def test_refuses_a_target_below_the_volume(lab):
    """Rule 1: grow only."""
    rid = lab.seed()
    refused(lab, lab.approve(rid, 128 * GIB), rid, "already", status="failed")


def test_refuses_when_the_datastore_moved_to_another_device(lab):
    rid = lab.seed(dev=OTHER_DEVICE)
    refused(lab, lab.approve(rid, 272 * GIB, dev=OTHER_DEVICE), rid, "is now on %s" % DEVICE, status="failed")


def test_refuses_a_device_that_is_not_an_everpure_one(lab):
    lab.edit_world(lambda w: w["vc"].update(device="naa.600508b1001c0d0e0f1011121314151617"))
    rid = lab.seed(dev="naa.600508b1001c0d0e0f1011121314151617")
    refused(lab, lab.approve(rid, 272 * GIB, dev="naa.600508b1001c0d0e0f1011121314151617"), rid,
            "not an Everpure FlashArray device", status="failed")


def test_refuses_a_datastore_with_two_extents(lab):
    lab.edit_world(lambda w: w["vc"].update(extents=[DEVICE, OTHER_DEVICE]))
    rid = lab.seed()
    refused(lab, lab.approve(rid, 272 * GIB), rid, "can't be grown automatically", status="failed")


def test_refuses_a_datastore_that_is_not_vmfs_6(lab):
    lab.edit_world(lambda w: w["vc"].update(vmfs_major_version=5))
    rid = lab.seed()
    refused(lab, lab.approve(rid, 272 * GIB), rid, "can't be grown automatically", status="failed")


def test_refuses_a_serial_that_is_on_no_array(lab):
    lab.edit_world(lambda w: w["arrays"][ARRAY]["volumes"][DS].update(serial="0" * 24))
    rid = lab.seed()
    refused(lab, lab.approve(rid, 272 * GIB), rid, "matches 0 volumes", status="failed")


def test_refuses_a_serial_that_is_on_two_volumes(lab):
    def change(world):
        world["arrays"][ARRAY]["volumes"]["copy"] = dict(world["arrays"][ARRAY]["volumes"][DS])
    lab.edit_world(change)
    rid = lab.seed()
    refused(lab, lab.approve(rid, 272 * GIB), rid, "matches 2 volumes", status="failed")


def test_refuses_a_volume_in_a_pod(lab):
    def change(world):
        arr = world["arrays"][ARRAY]
        arr["volumes"]["pod1::%s" % DS] = arr["volumes"].pop(DS)
    lab.edit_world(change)
    rid = lab.seed()
    proc = lab.approve(rid, 272 * GIB)
    assert proc.returncode != 0 and "in a pod (out of scope)" in out(proc), out(proc)
    assert writes(lab) == []


def test_a_volume_renamed_after_the_preflight_is_never_recreated(lab):
    """purefa_volume addresses the volume by name and would CREATE an empty one: the extend role re-reads it first."""
    lab.edit_world(lambda w: w.update(rename_before_write=True))
    rid = lab.seed()
    proc = lab.approve(rid, 272 * GIB)
    assert proc.returncode != 0 and "changed since the preflight" in out(proc), out(proc)
    assert lab.calls("purefa_volume") == [], "the write module was reached"
    assert lab.request(rid)["status"] == "failed"
    assert [c for c in lab.calls() if c["module"] in ("vmware_host_scanhba", "vmware_host_datastore")] == []


# ====================================================================== findings 3, 4, 5 (the request lifecycle)
def test_deny_is_recorded_and_blocks_a_later_approval(lab):
    """Finding 3: a deny used to live only in the bridge's memory; a Custom form opened before it could still grow."""
    rid = lab.seed()
    denied = lab.deny(rid)
    assert denied.returncode == 0, out(denied)
    request = lab.request(rid)
    assert request["status"] == "denied"
    assert request["denied"]["approver_id"] == APPROVER and request["denied"]["approver_name"] == "demo-approver"
    refused(lab, lab.approve(rid, 272 * GIB), rid, "request is denied", status="denied")
    # the bridge printed "Approved" over "Denied" for the late approval: the refusal puts the denial back
    updates = [json.dumps(b) for m, b in lab.slack.requests if m == "chat.update"]
    assert updates and "Denied" in updates[-1] and "afterwards was refused" in updates[-1], updates


@pytest.mark.parametrize("over,needle", [(dict(approver_id="U0INTRUDER"), "U0INTRUDER is not an approver"),
                                         (dict(vc="vcenter99.example.lab"), "identifiers differ"),
                                         (dict(ds="ds-other"), "identifiers differ"),
                                         (dict(dev=OTHER_DEVICE), "identifiers differ")])
def test_deny_from_a_stranger_or_for_other_identifiers_changes_nothing(lab, over, needle):
    rid = lab.seed()
    before = lab.request_bytes(rid)
    proc = lab.deny(rid, **over)
    assert proc.returncode != 0 and needle in out(proc), out(proc)
    assert lab.request_bytes(rid) == before


@pytest.mark.parametrize("status", ["executing", "done", "cleared", "denied"])
def test_deny_never_touches_a_request_that_was_acted_on(lab, status):
    rid = lab.seed(status=status)
    before = lab.request_bytes(rid)
    lab.deny(rid)
    assert lab.request_bytes(rid) == before


def test_a_failed_request_can_be_denied(lab):
    """A failed run offers the buttons again, Deny among them: that denial is recorded too."""
    rid = lab.seed(status="failed", lock=False)
    assert lab.deny(rid).returncode == 0
    assert lab.request(rid)["status"] == "denied"


def test_deny_of_an_unknown_request_does_nothing(lab):
    proc = lab.deny(str(uuid.uuid4()))
    assert proc.returncode != 0 and "is unknown: no proposal wrote it" in out(proc), out(proc)


def test_a_second_identical_approval_is_a_no_op(lab):
    """Finding 4: after `done`, the same approval again must change nothing: no rescan, no write, no new result."""
    rid, _ = lab.propose()
    assert lab.approve(rid, 272 * GIB).returncode == 0
    snapshot, calls_before = lab.request_bytes(rid), len(lab.calls())
    again = lab.approve(rid, 272 * GIB)
    assert again.returncode == 0, out(again)
    assert len(lab.calls()) == calls_before, "a replayed approval reached the array or the hosts: %s" % lab.calls()[calls_before:]
    assert lab.request_bytes(rid) == snapshot
    assert lab.volume_bytes() == 272 * GIB


def test_a_replayed_approval_releases_the_lock_a_killed_run_left(lab):
    """A run killed between "Done" and the lock release left the lock `executing` until it expires."""
    rid = lab.seed(status="done", lock=False, approval={"target_bytes": 272 * GIB})
    lab.lock_path().write_text(json.dumps({"rid": rid, "status": "executing", "expires": int(time.time()) + 3600}),
                               encoding="utf-8")
    before = lab.request_bytes(rid)
    proc = lab.approve(rid, 272 * GIB)
    assert proc.returncode == 0, out(proc)
    assert json.loads(lab.lock_path().read_text())["status"] == "released"
    assert lab.request_bytes(rid) == before and writes(lab) == [] and lab.calls() == []


def test_a_replayed_approval_never_touches_another_requests_lock(lab):
    rid = lab.seed(status="done", lock=False, approval={"target_bytes": 272 * GIB})
    other = {"rid": str(uuid.uuid4()), "status": "executing", "expires": int(time.time()) + 3600}
    lab.lock_path().write_text(json.dumps(other), encoding="utf-8")
    assert lab.approve(rid, 272 * GIB).returncode == 0
    assert json.loads(lab.lock_path().read_text()) == other


def test_an_executing_request_is_approved_again_only_for_its_own_size(lab):
    """A killed run leaves `executing`: the same approval finishes it (rule 9), another size is refused."""
    rid = lab.seed(status="executing", approval={"target_bytes": 272 * GIB})
    refused(lab, lab.approve(rid, 288 * GIB), rid, "one request, one approved size", status="executing")
    proc = lab.approve(rid, 272 * GIB)
    assert proc.returncode == 0, out(proc)
    assert lab.volume_bytes() == 272 * GIB and lab.request(rid)["status"] == "done"


@pytest.mark.parametrize("target", [288 * GIB, 256 * GIB])
def test_a_second_approval_with_another_size_is_refused(lab, target):
    """Finding 4: a larger target used to grow the datastore a second time under the same request."""
    rid, _ = lab.propose()
    assert lab.approve(rid, 272 * GIB).returncode == 0
    snapshot, calls_before = lab.request_bytes(rid), len(lab.calls())
    proc = lab.approve(rid, target)
    assert proc.returncode != 0, "accepted a second approval with another size"
    assert len(lab.calls()) == calls_before and lab.request_bytes(rid) == snapshot
    assert lab.volume_bytes() == 272 * GIB
    assert lab.request(rid)["status"] == "done"


def test_a_failed_request_may_be_approved_again_with_the_same_size(lab):
    """Rule 9: approving again finishes a partial run (the volume grown, VMFS not)."""
    lab.edit_world(lambda w: w["arrays"][ARRAY]["volumes"][DS].update(size=272 * GIB))
    lab.edit_world(lambda w: [h.update(device_size=272 * GIB) for h in w["vc"]["hosts"]])
    rid = lab.seed(status="failed", lock=False, approval={"target_bytes": 272 * GIB})
    lab.lock_path().write_text(json.dumps({"rid": rid, "status": "failed", "expires": 0}), encoding="utf-8")
    proc = lab.approve(rid, 272 * GIB)
    assert proc.returncode == 0, out(proc)
    assert lab.world()["vc"]["vmfs_capacity"] == 272 * GIB
    assert [c for c in lab.calls("purefa_volume") if c["changed"]] == [], "the volume was already at target"
    assert lab.request(rid)["status"] == "done"


def test_a_slack_error_after_the_work_is_done_does_not_turn_it_into_a_failure(lab):
    """Finding 5: the final Slack updates used to run inside the guarded block: one error rewrote `done` as `failed`."""
    rid, _ = lab.propose()
    lab.slack.fail_when = lambda method, body: "xpanded" in json.dumps(body)
    proc = lab.approve(rid, 272 * GIB)
    assert proc.returncode == 0, out(proc)
    assert lab.volume_bytes() == 272 * GIB
    request = lab.request(rid)
    assert request["status"] == "done" and "failure" not in request
    assert json.loads(lab.lock_path().read_text())["status"] == "released"
    assert not [t for t in lab.slack.texts("chat.postMessage") if "Expansion failed" in t]


# ====================================================================== tools/lock_check.sh reads what the real playbooks write
def lock_check(lab):
    return subprocess.run(["bash", str(ROOT / "tools" / "lock_check.sh"), str(lab.tmp)], capture_output=True, text=True)


def test_sync_sees_the_locks_the_real_playbooks_write(lab):
    """The checker is only as good as its reading of the real writer's files (a quoted or reformatted number...)."""
    assert lock_check(lab).returncode == 0, "nothing in flight yet"
    rid, proc = lab.propose()
    assert proc.returncode == 0, out(proc)
    assert lock_check(lab).returncode == 3, "a proposal waiting for a click: %s" % lab.lock_path().read_text()
    assert lab.approve(rid, 272 * GIB).returncode == 0
    assert lock_check(lab).returncode == 0, "released after the expansion: %s" % lab.lock_path().read_text()


def test_sync_sees_an_executing_lock_a_killed_run_left(lab):
    lab.seed(status="executing")
    assert lock_check(lab).returncode == 3


# ====================================================================== cleared.yml: only the request of the cancelled alert
def test_a_cancel_withdraws_the_proposal_of_that_alert(lab):
    rid = lab.seed()                                   # proposed for alert a1b2c3d4-0001-...
    assert lab.cancel().returncode == 0                # VCF Ops cancels alert a1b2c3d4-0001-...
    assert lab.request(rid)["status"] == "cleared"
    assert json.loads(lab.lock_path().read_text())["status"] == "released"


def test_a_cancel_of_an_older_alert_leaves_the_newer_proposal_alone(lab):
    """Every crossing of the limit is a new alert: the cancel of the first must not withdraw the second's proposal."""
    rid = lab.seed()
    before, lock_before = lab.request_bytes(rid), lab.lock_path().read_bytes()
    proc = lab.cancel(alert_id="a1b2c3d4-0000-4000-8000-00000000dead")
    assert proc.returncode == 0, out(proc)
    assert lab.request_bytes(rid) == before and lab.lock_path().read_bytes() == lock_before
    assert not lab.slack.requests, "nothing is edited in Slack either"


# ====================================================================== the alert link cannot inject Slack markup
@pytest.mark.parametrize("link", ["https://ops.example.lab/x>|<!channel>", "https://ops.example.lab/x|<!here>",
                                  "https://ops.example.lab/<@U0INTRUDER>", "javascript:alert(1)", "https://a b"])
def test_an_alert_link_cannot_inject_mrkdwn_into_the_proposal(lab, link):
    rid, proc = lab.propose(link=link)
    assert proc.returncode == 0, out(proc)
    posted = json.dumps(lab.slack.requests[0][1])
    for markup in ("<!channel>", "<!here>", "<@U0INTRUDER>", "javascript:"):
        assert markup not in posted, "%s reached Slack" % markup
    assert "VCF Operations alert" in posted           # the label stays, only the link is dropped


def test_an_alert_for_a_datastore_that_is_not_full_proposes_nothing(lab):
    """Finding 7: the alert names the datastore by name; one of the same name that is only half full is not the one."""
    lab.edit_world(lambda w: w["vc"].update(used=100 * GIB))          # 100 of 199 GiB = 50 %
    rid, proc = lab.propose()
    assert proc.returncode != 0 and "below the 75%" in out(proc), out(proc)
    assert list((lab.state / "requests").iterdir()) == [] and not lab.lock_path().exists()
    posted = json.dumps(lab.slack.requests)
    assert "could not propose" in posted and '"actions"' not in posted, "a warning, never buttons"


def test_the_minimum_can_be_turned_off_for_the_time_remaining_symptom(lab):
    lab.edit_world(lambda w: w["vc"].update(used=100 * GIB))
    payload = json.loads((FIX / "vcfops" / "new_datastore_80.json").read_text(encoding="utf-8"))
    rid = str(uuid.uuid4())
    proc = lab.run("propose.yml", lab.event("vcfops", payload, rid), extra={"dsgrow_alert_min_used_pct": 0})
    assert proc.returncode == 0, out(proc)
    assert lab.request(rid)["status"] == "proposed"


def test_a_clean_alert_link_is_kept(lab):
    rid, proc = lab.propose()
    assert proc.returncode == 0, out(proc)
    assert "<https://ops.example.lab/ui/index.action#/object/" in json.dumps(lab.slack.requests[0][1])


# ====================================================================== cleared.yml next to a denied request
def test_a_cancelled_alert_keeps_the_record_of_a_denied_request(lab):
    rid = lab.seed()
    assert lab.deny(rid).returncode == 0
    assert lab.cancel().returncode == 0
    request = lab.request(rid)
    assert request["status"] == "denied" and request["denied"]["approver_id"] == APPROVER
