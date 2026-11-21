"""Bridge tests with a fake Slack client and a fake sender (skill slack-approval-bridge "Tests that must exist")."""
import hashlib
import hmac
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions  # noqa: E402
import config  # noqa: E402
import messages  # noqa: E402
import rulebook_client  # noqa: E402
import validate  # noqa: E402

GIB = 1024 ** 3
NOW = 1_763_546_400
RID = "0b5e6f1c-7d2a-4e8b-9c3f-1a2b3c4d5e6f"
APPROVER = "U0DEMO0001"
STRANGER = "U0OTHER001"
CHANNEL, TS = "C0DEMO0001", "1763546400.000100"
SECRETS = {"SLACK_BOT_TOKEN": "xoxb-secret-bot", "SLACK_APP_TOKEN": "xapp-secret-app",
           "DSGROW_EDA_APPROVALS_TOKEN": "secret-approvals-token", "DSGROW_APPROVAL_HMAC_SECRET": "secret-hmac"}
ENV = dict(SECRETS, DSGROW_APPROVER_IDS=APPROVER + ", U0DEMO0002", DSGROW_SIZE_STEP_BYTES=str(64 * GIB))
BLOCKS = [{"type": "header", "text": {"type": "plain_text", "text": "x"}},
          {"type": "actions", "block_id": "dsgrow_actions", "elements": []},
          {"type": "context", "elements": []}]


def value(**over):
    v = {"rid": RID, "vc": "vcenter-w01.vcf9-1.lab", "ds": "vcf9-1-w02-demo01",
         "dev": "eui.00361019ecace43d24a93783000120a4", "cur": 512 * GIB, "exp": NOW + 3600, "tgt": 640 * GIB}
    v.update(over)
    return json.dumps({k: x for k, x in v.items() if x is not None})


class FakeSlack:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(**kw):
            self.calls.append((name, kw))
            return {"ok": True}
        return call

    def names(self):
        return [c[0] for c in self.calls]


class FakeSender:
    def __init__(self, fail=None):
        self.events, self.fail = [], fail

    def __call__(self, url, token, secret, event):
        if self.fail:
            raise rulebook_client.SendError(self.fail)
        self.events.append((url, event))
        return 200


@pytest.fixture
def cfg():
    return config.from_env(ENV)


def make(cfg, sender=None):
    slack, sender = FakeSlack(), sender or FakeSender()
    return actions.Bridge(cfg, slack, sender=sender, clock=lambda: NOW), slack, sender


def click(bridge, user=APPROVER, action_id="", label="", **over):
    return bridge.approve(user_id=user, user_name="demo", channel=CHANNEL, ts=TS, blocks=BLOCKS,
                          button_value=value(**over), action_id=action_id, label=label)


# ------------------------------------------------------------------ config
def test_config_requires_approvers():
    with pytest.raises(SystemExit):
        config.from_env(dict(ENV, DSGROW_APPROVER_IDS=" , "))


def test_config_refuses_non_loopback_url():
    with pytest.raises(SystemExit):
        config.from_env(dict(ENV, DSGROW_APPROVALS_URL="http://192.0.2.5:5081/approval"))


def test_config_repr_has_no_secrets(cfg):
    text = repr(cfg) + str(cfg)
    assert not any(s in text for s in SECRETS.values())


# ------------------------------------------------------------------ approve
def test_happy_path(cfg):
    bridge, slack, sender = make(cfg)
    assert click(bridge) is None
    assert len(sender.events) == 1
    url, event = sender.events[0]
    assert url == "http://127.0.0.1:5081/approval"
    assert event["schema"] == "dsgrow.approval.v1" and event["decision"] == "approve"
    assert event["tgt"] == 640 * GIB and event["approver_id"] == APPROVER and event["rid"] == RID
    assert slack.names() == ["chat_update", "chat_postMessage"]
    blocks = slack.calls[0][1]["blocks"]
    assert not [b for b in blocks if b["type"] == "actions"], "buttons must be gone"
    assert "Approved" in blocks[-1]["text"]["text"]


# ------------------------------------------------------------------ which button (owner 2026-11-20)
def test_label_and_value_must_agree(cfg):
    """A button whose label shows another size than the one it carries sends nothing."""
    bridge, slack, sender = make(cfg)
    reason = click(bridge, action_id="dsgrow_approve_2", label="Grow to 576 GiB (→ 62.4%)", tgt=640 * GIB)
    assert reason == "label and value differ"
    assert sender.events == [] and slack.names() == ["chat_postEphemeral"]
    assert "576 GiB" in slack.calls[0][1]["text"] and "640 GiB" in slack.calls[0][1]["text"]
    assert click(bridge, action_id="dsgrow_approve_2", label="Grow to 640 GiB (→ 62.4%)") is None
    assert len(sender.events) == 1


def test_label_rounding_is_not_a_mismatch(cfg):
    """Labels round to 2 decimals: 1040 GiB reads '1.02 TiB' and must still be sent."""
    bridge, slack, sender = make(cfg, )
    bridge.cfg = config.from_env(dict(ENV, DSGROW_SIZE_STEP_BYTES=str(16 * GIB), DSGROW_MAX_GROWTH_PCT="200"))
    assert actions.label_size("Grow to 1.02 TiB (→ 50%)") == validate.human(1040 * GIB) == "1.02 TiB"
    assert click(bridge, label="Grow to 1.02 TiB (→ 50%)", tgt=1040 * GIB) is None
    assert len(sender.events) == 1


def test_clicked_button_is_logged_and_quoted(cfg, caplog):
    bridge, slack, sender = make(cfg)
    with caplog.at_level(logging.INFO, logger="dsgrow.bridge"):
        assert click(bridge, action_id="dsgrow_approve_2", label="Grow to 640 GiB (→ 62.4%)") is None
    assert "dsgrow_approve_2" in caplog.text and "Grow to 640 GiB" in caplog.text and "640 GiB" in caplog.text
    thread = [kw for name, kw in slack.calls if name == "chat_postMessage"][0]["text"]
    assert "*640 GiB*" in thread and "Grow to 640 GiB" in thread


def test_label_without_size_is_not_checked(cfg):
    """Custom… and old messages carry no size in the label: the value alone decides, as before."""
    bridge, slack, sender = make(cfg)
    assert actions.label_size("Custom…") is None and actions.label_size("") is None
    assert click(bridge, action_id="dsgrow_approve_1", label="") is None
    assert len(sender.events) == 1


def test_unauthorised_user_changes_nothing(cfg):
    bridge, slack, sender = make(cfg)
    assert click(bridge, user=STRANGER) == "not an approver"
    assert sender.events == []
    assert slack.names() == ["chat_postEphemeral"]


def test_expired_request(cfg):
    bridge, slack, sender = make(cfg)
    assert "expired" in click(bridge, exp=NOW - 1)
    assert sender.events == [] and slack.names() == ["chat_postEphemeral"]


def test_size_not_above_current(cfg):
    bridge, _, sender = make(cfg)
    assert "grow only" in click(bridge, tgt=512 * GIB)
    assert sender.events == []


def test_size_over_cap(cfg):
    bridge, _, sender = make(cfg)
    assert "50%" in click(bridge, tgt=832 * GIB)
    assert sender.events == []


def test_non_step_size(cfg):
    bridge, _, sender = make(cfg)
    assert "multiple" in click(bridge, tgt=600 * GIB)
    assert sender.events == []


def test_malformed_identifiers(cfg):
    bridge, _, sender = make(cfg)
    assert "malformed" in click(bridge, dev="naa.600a098038303053")
    assert "malformed" in click(bridge, rid="../../etc/passwd")
    assert sender.events == []


def test_double_click_sends_once(cfg):
    bridge, slack, sender = make(cfg)
    assert click(bridge) is None
    assert click(bridge) == "claimed"
    assert len(sender.events) == 1
    assert slack.names()[-1] == "chat_postEphemeral"


def test_send_failure_restores_buttons(cfg):
    bridge, slack, _ = make(cfg, FakeSender(fail="the rulebook is not reachable (refused)"))
    assert "not reachable" in click(bridge)
    name, kw = slack.calls[1]
    assert name == "chat_update"
    assert [b for b in kw["blocks"] if b["type"] == "actions"], "buttons must be back"
    assert slack.calls[2][0] == "chat_postMessage" and slack.calls[2][1]["thread_ts"] == TS
    # and the claim is released: a retry can send
    bridge.sender = FakeSender()
    assert click(bridge) is None


# ------------------------------------------------------------------ deny
def test_deny_never_sends(cfg):
    bridge, slack, sender = make(cfg)
    assert bridge.deny(user_id=APPROVER, channel=CHANNEL, ts=TS, blocks=BLOCKS, button_value=value()) is None
    assert sender.events == []
    assert slack.names() == ["chat_update"]
    assert "Denied" in slack.calls[0][1]["blocks"][-1]["text"]["text"]


def test_deny_unauthorised(cfg):
    bridge, slack, sender = make(cfg)
    assert bridge.deny(user_id=STRANGER, channel=CHANNEL, ts=TS, blocks=BLOCKS, button_value=value()) == "not an approver"
    assert slack.names() == ["chat_postEphemeral"]


# ------------------------------------------------------------------ custom
def test_custom_opens_modal_then_approves(cfg):
    bridge, slack, sender = make(cfg)
    bridge.open_custom(trigger_id="t1", user_id=APPROVER, channel=CHANNEL, ts=TS, blocks=BLOCKS,
                       button_value=value(tgt=None))
    assert slack.names() == ["views_open"]
    view = slack.calls[0][1]["view"]
    meta = json.loads(view["private_metadata"])
    assert bridge.submit_custom(user_id=APPROVER, user_name="demo", metadata=meta, size_text="704 GiB") == {}
    assert bridge.approve_custom(user_id=APPROVER, user_name="demo", metadata=meta, size_text="704 GiB") is None
    assert sender.events[0][1]["tgt"] == 704 * GIB


@pytest.mark.parametrize("size,needle", [("abc", "GiB"), ("600 GiB", "multiple"), ("2 TiB", "50%"),
                                         ("512", "grow only")])
def test_custom_submit_errors(cfg, size, needle):
    bridge, _, sender = make(cfg)
    meta = {"value": value(tgt=None), "channel": CHANNEL, "ts": TS}
    errors = bridge.submit_custom(user_id=APPROVER, user_name="demo", metadata=meta, size_text=size)
    assert needle in errors[messages.SIZE_BLOCK_ID]
    assert sender.events == []


def test_custom_submit_unauthorised(cfg):
    bridge, _, _ = make(cfg)
    meta = {"value": value(tgt=None), "channel": CHANNEL, "ts": TS}
    assert bridge.submit_custom(user_id=STRANGER, user_name="x", metadata=meta, size_text="704")


# ------------------------------------------------------------------ the wire
def test_signature_matches_what_the_rulebook_verifies():
    body = rulebook_client.body_bytes({"schema": "dsgrow.approval.v1", "rid": RID})
    sig = rulebook_client.signature("secret-hmac", body)
    # ansible.eda.webhook: strips "sha256=" and compares hex HMAC-SHA256 of the raw body
    assert sig.startswith("sha256=")
    assert hmac.compare_digest(sig[len("sha256="):], hmac.new(b"secret-hmac", body, hashlib.sha256).hexdigest())


def test_client_refuses_other_schemas():
    with pytest.raises(rulebook_client.SendError):
        rulebook_client.send("http://127.0.0.1:1/x", "t", "s", {"schema": "something.else"})


def test_client_unreachable_message_has_no_secret():
    with pytest.raises(rulebook_client.SendError) as exc:
        rulebook_client.send("http://127.0.0.1:9/approval", "secret-approvals-token", "secret-hmac",
                             {"schema": "dsgrow.approval.v1"}, timeout=2)
    assert "secret" not in str(exc.value)


def test_tokens_never_logged(cfg, caplog):
    caplog.set_level(logging.DEBUG)
    bridge, _, _ = make(cfg)
    click(bridge)
    click(bridge, user=STRANGER)
    bridge2, _, _ = make(cfg, FakeSender(fail="the rulebook answered HTTP 401"))
    click(bridge2)
    assert not any(s in caplog.text for s in SECRETS.values())


# ------------------------------------------------------------------ Bolt wiring (no network)
def test_bolt_wiring_dispatches_a_click(cfg):
    from slack_bolt import App, BoltRequest
    from slack_bolt.authorization import AuthorizeResult

    def authorize(**_):   # local: no auth.test call to Slack
        return AuthorizeResult(enterprise_id=None, team_id="T0DEMO0001", bot_token="xoxb-test", bot_user_id="U0BOT")

    app = App(signing_secret="test", authorize=authorize, request_verification_enabled=False,
              process_before_response=True)
    bridge, slack, sender = make(cfg)
    actions.register(app, bridge)
    body = {"type": "block_actions", "team": {"id": "T0DEMO0001"},
            "user": {"id": APPROVER, "name": "demo", "team_id": "T0DEMO0001"}, "trigger_id": "t1",
            "container": {"type": "message", "channel_id": CHANNEL, "message_ts": TS},
            "channel": {"id": CHANNEL}, "message": {"ts": TS, "blocks": BLOCKS},
            "actions": [{"type": "button", "action_id": "dsgrow_approve_2", "block_id": "dsgrow_actions",
                         "text": {"type": "plain_text", "text": "Grow to 640 GiB (→ 62.4%)"},
                         "value": value()}]}
    req = BoltRequest(body="payload=" + __import__("urllib.parse").parse.quote(json.dumps(body)),
                      headers={"content-type": ["application/x-www-form-urlencoded"]})
    resp = app.dispatch(req)
    assert resp.status == 200
    assert len(sender.events) == 1 and sender.events[0][1]["tgt"] == 640 * GIB
    thread = [kw for name, kw in slack.calls if name == "chat_postMessage"][0]["text"]
    assert "Grow to 640 GiB" in thread, "the wiring passes the clicked button's label through"


def test_app_builds_like_production_without_signing_secret(cfg, monkeypatch):
    """app.build_app is what the container runs: it must start with only the bot token (Socket Mode, no signing
    secret). Only the startup auth.test call is switched off here, to stay offline."""
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    import app as bridge_app

    built = bridge_app.build_app(cfg, token_verification=False)
    assert built is not None


# ------------------------------------------------------------------ parity with the filter plugin
def test_parse_size_matches_filter():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = importlib.util.spec_from_file_location("f", os.path.join(root, "plugins", "filter", "dsgrow.py"))
    f = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f)
    for text in ["704", "704 GiB", "704G", "1.5 TiB", "1T", " 2 tib ", "0.25 TiB"]:
        assert validate.parse_size(text) == f.dsgrow_parse_size(text), text
    for text in ["", "abc", "-5 GiB", "5 PB", "0.3 GiB"]:
        with pytest.raises(validate.Invalid):
            validate.parse_size(text)
