"""Pure functions: parse a button's request, parse a size, check it (fast feedback; execute.yml is the authority).

Size parsing and the cap checks mirror plugins/filter/dsgrow.py; tests/unit and bridge/tests pin both to the same
cases.
"""
from __future__ import annotations

import dataclasses
import json
import re

GIB = 1024 ** 3
TIB = 1024 ** 4
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DEV = re.compile(r"^(naa\.624a9370[0-9a-f]{24}|eui\.00[0-9a-f]{14}24a937[0-9a-f]{10})$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,79}$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_SIZE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(t|tb|tib|g|gb|gib)?\s*$", re.IGNORECASE)


class Invalid(ValueError):
    """A request or size the bridge refuses; the message is shown to the user."""


@dataclasses.dataclass(frozen=True)
class Request:
    rid: str
    vc: str
    ds: str
    dev: str
    cur: int
    exp: int
    tgt: int | None = None


def human(value: int) -> str:
    value = int(value)
    for unit, factor in (("TiB", TIB), ("GiB", GIB)):
        if value >= factor:
            return "%s %s" % (("%.2f" % (value / factor)).rstrip("0").rstrip("."), unit)
    return "%d B" % value


def parse_request(value: str) -> Request:
    """The JSON a proposal button carries (roles/dsgrow_slack/templates/proposal.json.j2)."""
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        raise Invalid("this button carries no readable request")
    if not isinstance(data, dict):
        raise Invalid("this button carries no readable request")
    try:
        req = Request(rid=str(data["rid"]), vc=str(data["vc"]), ds=str(data["ds"]), dev=str(data["dev"]),
                      cur=int(data["cur"]), exp=int(data["exp"]),
                      tgt=int(data["tgt"]) if data.get("tgt") is not None else None)
    except (KeyError, TypeError, ValueError):
        raise Invalid("this button's request is incomplete")
    if not _UUID.match(req.rid) or not _DEV.match(req.dev) or not _NAME.match(req.ds) or not _HOST.match(req.vc):
        raise Invalid("this button's request has malformed identifiers")
    if req.cur <= 0 or (req.tgt is not None and req.tgt <= 0):
        raise Invalid("this button's request has malformed sizes")
    return req


def parse_size(text: str) -> int:
    """'704', '704 GiB', '704G', '1.5 TiB' -> bytes (plain numbers are GiB; GB/TB read as GiB/TiB)."""
    m = _SIZE.match(str(text or ""))
    if not m:
        raise Invalid("use a number with GiB or TiB, e.g. 704 GiB or 1.5 TiB")
    number, unit = m.group(1), (m.group(2) or "g").lower()
    factor = TIB if unit.startswith("t") else GIB
    if "." in number:
        whole, frac = number.split(".")
        num = int(whole) * factor * 10 ** len(frac) + int(frac) * factor
        if num % 10 ** len(frac):
            raise Invalid("%s is not a whole number of bytes" % text.strip())
        return num // 10 ** len(frac)
    return int(number) * factor


def check(req: Request, target: int, caps, now: int) -> list:
    """Every reason the bridge refuses this target (empty = send it to the rulebook)."""
    reasons = []
    if now >= req.exp:
        reasons.append("the request expired; wait for a new proposal")
    if target <= req.cur:
        reasons.append("%s is not larger than the current %s (grow only)" % (human(target), human(req.cur)))
    if target % caps.size_step_bytes:
        reasons.append("%s is not a multiple of %s" % (human(target), human(caps.size_step_bytes)))
    growth = target - req.cur
    if growth > 0 and growth * 100 > req.cur * caps.max_growth_pct:
        reasons.append("growth %s is more than %d%% of %s" % (human(growth), caps.max_growth_pct, human(req.cur)))
    if growth > caps.max_growth_bytes:
        reasons.append("growth %s is more than the per-request cap %s" % (human(growth), human(caps.max_growth_bytes)))
    if target > caps.max_volume_bytes:
        reasons.append("%s is above the maximum volume size %s" % (human(target), human(caps.max_volume_bytes)))
    return reasons
