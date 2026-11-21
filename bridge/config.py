"""Bridge settings from the environment (the env file setup/deploy.yml renders from the dsgrow vault and inventory)."""
from __future__ import annotations

import dataclasses
import os

GIB = 1024 ** 3


@dataclasses.dataclass(frozen=True)
class Caps:
    """A copy of the policy caps for fast feedback in Slack. execute.yml stays the authority."""
    size_step_bytes: int = 256 * GIB
    max_growth_pct: int = 50
    max_growth_bytes: int = 10 * 1024 ** 4
    max_volume_bytes: int = 64 * 1024 ** 4


@dataclasses.dataclass(frozen=True)
class Config:
    slack_bot_token: str
    slack_app_token: str
    approver_ids: frozenset
    approvals_url: str
    approvals_token: str
    hmac_secret: str
    caps: Caps
    claim_seconds: int = 30

    def __repr__(self):  # never print secrets
        return "Config(approvers=%d, approvals_url=%s, caps=%r)" % (len(self.approver_ids), self.approvals_url, self.caps)

    __str__ = __repr__


def _required(env, name):
    value = env.get(name, "").strip()
    if not value:
        raise SystemExit("environment variable %s is required" % name)
    return value


def from_env(env=None) -> Config:
    env = os.environ if env is None else env
    ids = frozenset(x.strip() for x in env.get("DSGROW_APPROVER_IDS", "").split(",") if x.strip())
    if not ids:
        raise SystemExit("DSGROW_APPROVER_IDS is empty: nobody could approve")
    caps = Caps(
        size_step_bytes=int(env.get("DSGROW_SIZE_STEP_BYTES", Caps.size_step_bytes)),
        max_growth_pct=int(env.get("DSGROW_MAX_GROWTH_PCT", Caps.max_growth_pct)),
        max_growth_bytes=int(env.get("DSGROW_MAX_GROWTH_BYTES", Caps.max_growth_bytes)),
        max_volume_bytes=int(env.get("DSGROW_MAX_VOLUME_BYTES", Caps.max_volume_bytes)),
    )
    url = env.get("DSGROW_APPROVALS_URL", "http://127.0.0.1:5081/approval")
    if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:"):
        raise SystemExit("DSGROW_APPROVALS_URL must be the rulebook's loopback approvals source")
    return Config(
        slack_bot_token=_required(env, "SLACK_BOT_TOKEN"),
        slack_app_token=_required(env, "SLACK_APP_TOKEN"),
        approver_ids=ids,
        approvals_url=url,
        approvals_token=_required(env, "DSGROW_EDA_APPROVALS_TOKEN"),
        hmac_secret=_required(env, "DSGROW_APPROVAL_HMAC_SECRET"),
        caps=caps,
    )
