"""Send a dsgrow.approval.v1 event to the rulebook's loopback `approvals` source (token + HMAC-SHA256 of the body)."""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request

SCHEMA = "dsgrow.approval.v1"
SIGNATURE_HEADER = "x-dsgrow-signature"
# The event carries the approvals token and an HMAC of the body, and goes to a loopback address: never through a proxy
# named by HTTP_PROXY / ALL_PROXY in the container's environment.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SendError(RuntimeError):
    """The rulebook did not accept the event. The message never contains a secret."""


def body_bytes(event: dict) -> bytes:
    return json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")


def signature(secret: str, body: bytes) -> str:
    """What ansible.eda.webhook verifies with hmac_algo sha256, hmac_format hex (a 'sha256=' prefix is stripped)."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def send(url: str, token: str, secret: str, event: dict, timeout: float = 10.0) -> int:
    if event.get("schema") != SCHEMA:
        raise SendError("refusing to send an event that is not %s" % SCHEMA)
    body = body_bytes(event)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header(SIGNATURE_HEADER, signature(secret, body))
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise SendError("the rulebook answered HTTP %d" % resp.status)
            return resp.status
    except urllib.error.HTTPError as exc:
        raise SendError("the rulebook answered HTTP %d" % exc.code) from None
    except (urllib.error.URLError, OSError) as exc:
        raise SendError("the rulebook is not reachable (%s)" % getattr(exc, "reason", exc)) from None
