"""Approve / custom / deny. Plain functions over a Slack client and a sender, so tests drive them without Bolt.

Order for every click (skill slack-approval-bridge): ack (done by the Bolt wrapper before calling these), authorise,
validate, claim (no double click), update the message (buttons gone), then send the approval event. A failed send
puts the buttons back.
"""
from __future__ import annotations

import logging
import re
import threading
import time

import messages
import rulebook_client
from validate import Invalid, Request, check, human, parse_request, parse_size

log = logging.getLogger("dsgrow.bridge")

# A size button's label, as proposal.json.j2 renders it ("Grow to 272 GiB (→ 62.4%)")
_LABEL_SIZE = re.compile(r"Grow to ([0-9]+(?:\.[0-9]+)?)\s*([GT]iB)\b")


def label_size(label: str):
    """The size a button's label shows, as text ("272 GiB"), or None when it shows none (Custom…, an old message).
    Compared as text with human(target): the label rounds to 2 decimals (1040 GiB reads "1.02 TiB"), and the
    playbooks' dsgrow_human_bytes and validate.human format alike (tests/offline/test_proposal.py pins it)."""
    m = _LABEL_SIZE.search(str(label or ""))
    return "%s %s" % (m.group(1), m.group(2)) if m else None


class Claims:
    """Short claims per request id: a second click within `seconds` finds the request claimed and does nothing.
    Short on purpose: after a failed run execute.yml offers the buttons again, and a click then must work."""

    def __init__(self, seconds: int, clock=time.monotonic):
        self._seconds, self._clock, self._lock, self._until = seconds, clock, threading.Lock(), {}

    def take(self, rid: str) -> bool:
        with self._lock:
            now = self._clock()
            if self._until.get(rid, 0) > now:
                return False
            self._until[rid] = now + self._seconds
            return True

    def release(self, rid: str) -> None:
        with self._lock:
            self._until.pop(rid, None)


class Bridge:
    def __init__(self, cfg, slack, sender=rulebook_client.send, clock=time.time):
        self.cfg, self.slack, self.sender, self.clock = cfg, slack, sender, clock
        self.claims = Claims(cfg.claim_seconds)
        self.blocks_cache = {}   # rid -> proposal blocks, for the custom modal (a view has no message)

    # ------------------------------------------------------------------ helpers
    def _ephemeral(self, channel, user, text):
        self.slack.chat_postEphemeral(channel=channel, user=user, text=text)

    def _authorised(self, user_id) -> bool:
        return user_id in self.cfg.approver_ids

    # ------------------------------------------------------------------ approve (a size button or the modal)
    def approve(self, *, user_id, user_name, channel, ts, blocks, button_value, target=None, action_id="", label=""):
        """Returns None when the event was sent, else the reason shown to the user.

        action_id / label: the clicked button as Slack reports it (owner 2026-11-20: twice the size sent was not the
        size the approver meant). Both are logged and quoted in the thread; a label that shows a size must show the
        size the button carries, else nothing is sent."""
        if not self._authorised(user_id):
            log.info("approve refused: %s is not an approver", user_id)
            self._ephemeral(channel, user_id, ":no_entry: You are not an approver for datastore expansions.")
            return "not an approver"
        try:
            req = parse_request(button_value)
        except Invalid as exc:
            self._ephemeral(channel, user_id, ":warning: %s" % exc)
            return str(exc)
        target = req.tgt if target is None else target
        if target is None:
            self._ephemeral(channel, user_id, ":warning: no size chosen")
            return "no size"
        shown = label_size(label)
        if shown is not None and shown != human(target):
            log.warning("approve refused for %s: button %s reads %r but carries %d bytes", req.rid, action_id, label,
                        target)
            self._ephemeral(channel, user_id, ":no_entry: Not sent: the button reads *%s* but carries *%s*. Nothing "
                                              "changed; use Custom… or wait for a new proposal."
                            % (shown, human(target)))
            return "label and value differ"
        reasons = check(req, target, self.cfg.caps, int(self.clock()))
        if reasons:
            self._ephemeral(channel, user_id, ":no_entry: Not sent: " + "; ".join(reasons))
            return "; ".join(reasons)
        if not self.claims.take(req.rid):
            self._ephemeral(channel, user_id, "This request is already being handled.")
            return "claimed"

        who = "<@%s>" % user_id
        self.slack.chat_update(channel=channel, ts=ts, text="Approved: %s" % human(target),
                               blocks=messages.status_blocks(blocks, ":hourglass_flowing_sand: *Approved* by %s for "
                                                             "*%s*, handing over to Ansible…" % (who, human(target))))
        event = {
            "schema": rulebook_client.SCHEMA, "decision": "approve", "rid": req.rid, "vc": req.vc, "ds": req.ds,
            "dev": req.dev, "tgt": target, "approver_id": user_id, "approver_name": str(user_name or "")[:80],
            "channel": channel, "ts": ts, "sent_at": int(self.clock()),
        }
        try:
            self.sender(self.cfg.approvals_url, self.cfg.approvals_token, self.cfg.hmac_secret, event)
        except rulebook_client.SendError as exc:
            log.warning("approval %s not delivered: %s", req.rid, exc)
            self.slack.chat_update(channel=channel, ts=ts, text="Approval not delivered",
                                   blocks=messages.status_blocks(blocks, ":warning: Approval by %s could not be handed "
                                                                 "over (%s). Nothing changed; try again." % (who, exc),
                                                                 keep_actions=True))
            self.slack.chat_postMessage(channel=channel, thread_ts=ts,
                                        text=":warning: Approval by %s not delivered: %s" % (who, exc))
            self.claims.release(req.rid)
            return str(exc)
        log.info("approval %s sent: %s %s -> %d bytes (%s) by %s, button %s %r", req.rid, req.vc, req.ds, target,
                 human(target), user_id, action_id or "-", label)
        how = (" (button “%s”)" % messages.escape(label)) if label else ""
        self.slack.chat_postMessage(channel=channel, thread_ts=ts,
                                    text=":white_check_mark: %s approved *%s*%s for %s. The rulebook runs the "
                                         "expansion; progress follows here."
                                         % (who, human(target), how, messages.escape(req.ds)))
        return None

    # ------------------------------------------------------------------ custom size
    def open_custom(self, *, trigger_id, user_id, channel, ts, blocks, button_value):
        """Open the modal first (trigger_id lives 3 s), check the rest on submit."""
        try:
            req = parse_request(button_value)
        except Invalid as exc:
            self._ephemeral(channel, user_id, ":warning: %s" % exc)
            return
        self.blocks_cache[req.rid] = blocks
        self.slack.views_open(trigger_id=trigger_id,
                              view=messages.custom_modal(req, button_value, channel, ts, self.cfg.caps.size_step_bytes))

    def submit_custom(self, *, user_id, user_name, metadata, size_text):
        """Returns {} when accepted (the modal closes), else {block_id: error} shown in the modal."""
        if not self._authorised(user_id):
            return {messages.SIZE_BLOCK_ID: "You are not an approver for datastore expansions."}
        try:
            target = parse_size(size_text)
            req = parse_request(metadata["value"])
        except (Invalid, KeyError, TypeError) as exc:
            return {messages.SIZE_BLOCK_ID: str(exc) or "invalid request"}
        reasons = check(req, target, self.cfg.caps, int(self.clock()))
        if reasons:
            return {messages.SIZE_BLOCK_ID: "; ".join(reasons)[:150]}
        return {}

    def approve_custom(self, *, user_id, user_name, metadata, size_text):
        """After submit_custom accepted: the same path as a size button."""
        blocks = self.blocks_cache.get(parse_request(metadata["value"]).rid, [])
        return self.approve(user_id=user_id, user_name=user_name, channel=metadata["channel"], ts=metadata["ts"],
                            blocks=blocks, button_value=metadata["value"], target=parse_size(size_text),
                            action_id="dsgrow_custom", label="Custom: %s" % str(size_text).strip()[:40])

    # ------------------------------------------------------------------ deny
    def deny(self, *, user_id, channel, ts, blocks, button_value):
        if not self._authorised(user_id):
            self._ephemeral(channel, user_id, ":no_entry: You are not an approver for datastore expansions.")
            return "not an approver"
        try:
            req = parse_request(button_value)
        except Invalid as exc:
            self._ephemeral(channel, user_id, ":warning: %s" % exc)
            return str(exc)
        if not self.claims.take(req.rid):
            self._ephemeral(channel, user_id, "This request is already being handled.")
            return "claimed"
        who = "<@%s>" % user_id
        self.slack.chat_update(channel=channel, ts=ts, text="Denied",
                               blocks=messages.status_blocks(blocks, ":no_entry_sign: *Denied* by %s. Nothing changed; "
                                                             "no new proposal for this datastore until the request "
                                                             "expires." % who))
        log.info("request %s denied by %s", req.rid, user_id)
        return None


def _value(body):
    return body["actions"][0]["value"]


def _clicked(body):
    """The clicked button's action_id and label, as Slack reports them."""
    action = body["actions"][0]
    return action.get("action_id", ""), (action.get("text") or {}).get("text", "")


def register(app, bridge):
    """Wire the handlers into a Bolt app. One Bridge for the process: its claims and block cache are shared."""
    import re

    @app.action(re.compile(r"^dsgrow_approve_\d+$"))
    def on_approve(ack, body):
        ack()
        action_id, label = _clicked(body)
        bridge.approve(
            user_id=body["user"]["id"], user_name=body["user"].get("name") or body["user"].get("username"),
            channel=body["container"]["channel_id"], ts=body["container"]["message_ts"],
            blocks=body.get("message", {}).get("blocks", []), button_value=_value(body),
            action_id=action_id, label=label)

    @app.action("dsgrow_custom")
    def on_custom(ack, body):
        ack()
        bridge.open_custom(
            trigger_id=body["trigger_id"], user_id=body["user"]["id"], channel=body["container"]["channel_id"],
            ts=body["container"]["message_ts"], blocks=body.get("message", {}).get("blocks", []),
            button_value=_value(body))

    @app.view(messages.CUSTOM_CALLBACK)
    def on_custom_submit(ack, body, view):
        import json
        user = body["user"]
        try:
            metadata = json.loads(view["private_metadata"])
        except (KeyError, ValueError):
            ack(response_action="errors", errors={messages.SIZE_BLOCK_ID: "this form lost its request; start again"})
            return
        size = view["state"]["values"][messages.SIZE_BLOCK_ID][messages.SIZE_ACTION_ID]["value"]
        errors = bridge.submit_custom(user_id=user["id"], user_name=user.get("name"), metadata=metadata, size_text=size)
        if errors:
            ack(response_action="errors", errors=errors)
            return
        ack()
        bridge.approve_custom(user_id=user["id"], user_name=user.get("name") or user.get("username"),
                              metadata=metadata, size_text=size)

    @app.action("dsgrow_deny")
    def on_deny(ack, body):
        ack()
        bridge.deny(
            user_id=body["user"]["id"], channel=body["container"]["channel_id"], ts=body["container"]["message_ts"],
            blocks=body.get("message", {}).get("blocks", []), button_value=_value(body))
