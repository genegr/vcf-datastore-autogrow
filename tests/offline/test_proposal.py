"""Cross-check: every button the playbooks render (tests/offline/logic.yml -> proposal.json) parses in the bridge."""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bridge"))
import validate  # noqa: E402

PROPOSAL = pathlib.Path(os.environ.get("DSGROW_STATE_DIR", "/tmp/dsgrow-offline")) / "proposal.json"


@pytest.mark.skipif(not PROPOSAL.exists(), reason="run tests/offline/logic.yml first (tools/offline.sh)")
def test_every_button_parses_in_the_bridge():
    blocks = json.loads(PROPOSAL.read_text())
    actions = [b for b in blocks if b["type"] == "actions"][0]["elements"]
    for button in actions:
        req = validate.parse_request(button["value"])
        assert req.ds == "dsgrow-demo01" and req.dev.startswith("eui.")
        assert len(button["value"]) <= 2000          # Slack's limit for a button value
        if button["action_id"].startswith("dsgrow_approve_"):
            assert req.tgt and req.tgt > req.cur
            # owner 2026-11-20: no default-styled size, a confirmation naming the size, and the label's size is the
            # size the button carries (the bridge refuses a mismatch)
            assert "style" not in button
            conf = button["confirm"]
            assert validate.human(req.tgt) in conf["title"]["text"] and validate.human(req.tgt) in conf["confirm"]["text"]
            assert len(conf["title"]["text"]) <= 100 and len(conf["text"]["text"]) <= 300
            assert len(conf["confirm"]["text"]) <= 30 and len(conf["deny"]["text"]) <= 30
            import actions  # noqa: E402  (bridge/, on sys.path above)
            assert actions.label_size(button["text"]["text"]) == validate.human(req.tgt)
        else:
            assert req.tgt is None
