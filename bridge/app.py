"""dsgrow Slack bridge: Bolt for Python over Socket Mode (outbound WebSocket only, nothing exposed).

Wiring only; the logic is in actions.py. Run: python app.py (environment: config.py).
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slack_bolt import App  # noqa: E402
from slack_bolt.adapter.socket_mode import SocketModeHandler  # noqa: E402

import actions  # noqa: E402
import config  # noqa: E402


def build_app(cfg, token_verification: bool = True) -> App:
    """The Bolt app with the dsgrow handlers.

    Socket Mode: Slack delivers events over the WebSocket opened with the app-level token, so there are no inbound HTTP
    requests and no signing secret; Bolt's HTTP request-signature middleware is therefore off (it refuses to start
    without a signing secret - measured on the run host 2026-11-20).
    """
    app = App(token=cfg.slack_bot_token, request_verification_enabled=False,
              token_verification_enabled=token_verification)
    actions.register(app, actions.Bridge(cfg, app.client))
    return app


def main():
    logging.basicConfig(level=os.environ.get("DSGROW_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # slack_sdk logs request bodies at DEBUG; keep it at INFO whatever the bridge's level is
    logging.getLogger("slack_sdk").setLevel(logging.INFO)
    logging.getLogger("slack_bolt").setLevel(logging.INFO)
    cfg = config.from_env()
    app = build_app(cfg)
    logging.getLogger("dsgrow.bridge").info("starting: %s", cfg)
    SocketModeHandler(app, cfg.slack_app_token).start()


if __name__ == "__main__":
    main()
