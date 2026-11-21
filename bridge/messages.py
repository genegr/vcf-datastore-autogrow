"""Block Kit pieces the bridge sends (the proposal itself is built by roles/dsgrow_slack)."""
from __future__ import annotations

import json

from validate import Request, human

STATUS_BLOCK_ID = "dsgrow_status"
SIZE_BLOCK_ID = "dsgrow_size"
SIZE_ACTION_ID = "dsgrow_size_input"
CUSTOM_CALLBACK = "dsgrow_custom_submit"


def escape(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def status_blocks(blocks, text, keep_actions=False):
    """The proposal in its order, without earlier status lines (and buttons unless keep_actions), plus `text`."""
    out = [b for b in (blocks or [])
           if b.get("block_id") != STATUS_BLOCK_ID and (keep_actions or b.get("type") != "actions")]
    out.append({"type": "section", "block_id": STATUS_BLOCK_ID, "text": {"type": "mrkdwn", "text": text}})
    return out


def custom_modal(req: Request, button_value: str, channel: str, ts: str, step: int) -> dict:
    meta = json.dumps({"value": button_value, "channel": channel, "ts": ts}, separators=(",", ":"))
    return {
        "type": "modal",
        "callback_id": CUSTOM_CALLBACK,
        "private_metadata": meta,
        "title": {"type": "plain_text", "text": "Custom size"},
        "submit": {"type": "plain_text", "text": "Approve"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text":
                "*%s* on %s is %s now. Enter the new absolute size (a multiple of %s)."
                % (escape(req.ds), escape(req.vc), human(req.cur), human(step))}},
            {"type": "input", "block_id": SIZE_BLOCK_ID, "label": {"type": "plain_text", "text": "New size"},
             "element": {"type": "plain_text_input", "action_id": SIZE_ACTION_ID,
                         "placeholder": {"type": "plain_text", "text": "e.g. 704 GiB or 1.5 TiB"}}},
        ],
    }
