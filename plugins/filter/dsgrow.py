# -*- coding: utf-8 -*-
"""dsgrow filters: the business logic of the expansion, as pure functions.

Everything here is unit-tested (tests/unit/test_filter_dsgrow.py) and has no side effects. Playbooks stay thin and
call these for device/serial mapping, size options, cap checks, expiry and human-readable units.

Device IDs of Everpure FlashArray volumes (serial = 24 hex digits):
    SCSI  naa.624a9370<serial>                                  (Everpure docs; CLAUDE.md rule 2)
    NVMe  eui.00<serial[0:14]>24a937<serial[14:24]>             (NGUID; proven by tests/probes/eui_serial.yml)
"""
from __future__ import annotations

import hashlib
import math
import re

try:  # ansible is not needed to unit-test the logic
    from ansible.errors import AnsibleFilterError
except ImportError:  # pragma: no cover - exercised only without ansible installed
    class AnsibleFilterError(Exception):
        pass

GIB = 1024 ** 3
TIB = 1024 ** 4
NAA_PREFIX = "naa.624a9370"
EUI_OUI = "24a937"
_HEX = re.compile(r"^[0-9a-f]+$")
_SERIAL = re.compile(r"^[0-9A-Fa-f]{24}$")


# ---------------------------------------------------------------------------------------------- device <-> serial
def dsgrow_is_everpure_device(device):
    """True when the device ID has one of the Everpure layouts."""
    try:
        dsgrow_device_to_fa_serial(device)
        return True
    except AnsibleFilterError:
        return False


def dsgrow_device_to_fa_serial(device):
    """naa.624a9370<24 hex> or eui.00<14 hex>24a937<10 hex> -> the array's volume serial (upper case)."""
    if not isinstance(device, str):
        raise AnsibleFilterError("device id must be a string, got %r" % (device,))
    dev = device.strip().lower()
    if dev.startswith(NAA_PREFIX):
        serial = dev[len(NAA_PREFIX):]
        if len(serial) == 24 and _HEX.match(serial):
            return serial.upper()
        raise AnsibleFilterError("naa device %r: expected naa.624a9370 + 24 hex digits" % device)
    if dev.startswith("eui."):
        nguid = dev[4:]
        if len(nguid) == 32 and _HEX.match(nguid) and nguid.startswith("00") and nguid[16:22] == EUI_OUI:
            return (nguid[2:16] + nguid[22:32]).upper()
        raise AnsibleFilterError("eui device %r: not the Everpure NVMe layout eui.00<14>24a937<10>" % device)
    raise AnsibleFilterError("device %r is not an Everpure FlashArray device (naa.624a9370 or eui.00..24a937..)"
                             % device)


def dsgrow_fa_serial_to_device(serial, kind="naa"):
    """Volume serial -> the device ID ESX shows for it: kind 'naa' (SCSI) or 'eui' (NVMe)."""
    if not isinstance(serial, str) or not _SERIAL.match(serial.strip()):
        raise AnsibleFilterError("serial %r: expected 24 hex digits" % (serial,))
    s = serial.strip().lower()
    if kind == "naa":
        return NAA_PREFIX + s
    if kind == "eui":
        return "eui.00" + s[:14] + EUI_OUI + s[14:]
    raise AnsibleFilterError("kind must be 'naa' or 'eui', got %r" % (kind,))


# ---------------------------------------------------------------------------------------------- units
def dsgrow_roundup(value, step):
    """Round value up to a multiple of step (both bytes)."""
    value, step = int(value), int(step)
    if step <= 0:
        raise AnsibleFilterError("step must be > 0")
    return -(-value // step) * step


def dsgrow_human_bytes(value):
    """Binary units for humans: 640 GiB, 1.25 TiB, 512 MiB. Bytes stay bytes internally."""
    value = int(value)
    for unit, factor in (("TiB", TIB), ("GiB", GIB), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if abs(value) >= factor:
            text = ("%.2f" % (value / factor)).rstrip("0").rstrip(".")
            return "%s %s" % (text, unit)
    return "%d B" % value


_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(t|tb|tib|g|gb|gib)?\s*$", re.IGNORECASE)
MAX_SIZE_BYTES = 1024 ** 5     # 1024 TiB: the same bound as bridge/validate.py (a typo, not a size)


def dsgrow_parse_size(text):
    """'704', '704 GiB', '704G', '1.5 TiB' -> bytes. Plain numbers are GiB. Decimal units are read as binary
    (an admin typing 'GB' means what vSphere shows). Raises on anything else, and on non-whole bytes."""
    m = _SIZE_RE.match(str(text))
    if not m:
        raise AnsibleFilterError("size %r: use a number with GiB or TiB, e.g. 704 GiB or 1.5 TiB" % (text,))
    number, unit = m.group(1), (m.group(2) or "g").lower()
    factor = TIB if unit.startswith("t") else GIB
    # exact arithmetic on the decimal string
    if "." in number:
        whole, frac = number.split(".")
        value = int(whole) * factor + int(frac) * factor // (10 ** len(frac))
        if int(frac) * factor % (10 ** len(frac)):
            raise AnsibleFilterError("size %r is not a whole number of bytes" % (text,))
    else:
        value = int(number) * factor
    if value > MAX_SIZE_BYTES:
        raise AnsibleFilterError("size %r is out of range (at most 1024 TiB)" % (text,))
    return value


# ---------------------------------------------------------------------------------------------- caps
def dsgrow_check_target(target_bytes, current_bytes, policy, array=None):
    """Every reason the target is refused (empty list = allowed).

    policy: dict with size_step_bytes, max_growth_pct, max_growth_bytes, max_volume_bytes,
            fa_min_free_pct_after
    array:  optional dict with capacity_bytes, used_bytes (physical) for the headroom check
    """
    target, current = int(target_bytes), int(current_bytes)
    reasons = []
    step = int(policy["size_step_bytes"])
    if target <= current:
        reasons.append("target %s is not larger than the current size %s (grow only)"
                       % (dsgrow_human_bytes(target), dsgrow_human_bytes(current)))
    if target % step:
        reasons.append("target %s is not a multiple of the %s step"
                       % (dsgrow_human_bytes(target), dsgrow_human_bytes(step)))
    growth = target - current
    if current > 0 and growth > 0 and growth * 100 > current * int(policy["max_growth_pct"]):
        reasons.append("growth %s is more than %s%% of the current size %s"
                       % (dsgrow_human_bytes(growth), policy["max_growth_pct"], dsgrow_human_bytes(current)))
    if growth > int(policy["max_growth_bytes"]):
        reasons.append("growth %s is more than the per-request cap %s"
                       % (dsgrow_human_bytes(growth), dsgrow_human_bytes(policy["max_growth_bytes"])))
    if target > int(policy["max_volume_bytes"]):
        reasons.append("target %s is above the maximum volume size %s"
                       % (dsgrow_human_bytes(target), dsgrow_human_bytes(policy["max_volume_bytes"])))
    if array is not None and growth > 0:
        free_pct = dsgrow_array_free_pct_after(array, growth)
        if free_pct < float(policy["fa_min_free_pct_after"]):
            reasons.append("array free space after growth would be %.1f%%, below the required %s%%"
                           % (free_pct, policy["fa_min_free_pct_after"]))
    return reasons


def dsgrow_array_free_pct_after(array, growth_bytes):
    """Physical free percent of the array if the growth were fully written (conservative for thin volumes)."""
    capacity = int(array["capacity_bytes"])
    if capacity <= 0:
        raise AnsibleFilterError("array capacity must be > 0")
    free_after = capacity - int(array["used_bytes"]) - max(int(growth_bytes), 0)
    return round(free_after * 100.0 / capacity, 2)


def dsgrow_size_options(used_bytes, current_bytes, utilizations, policy, array=None):
    """Size options for the Slack message.

    target = roundup(used / u, step). An option is offered when target > current and every cap holds; options that
    round to the same target are offered once (the first utilisation wins). Returns
    {"options": [{u, target_bytes, growth_bytes, utilization_after_pct, label}], "refused": [{u, target_bytes,
    reasons}]}.
    """
    used, current = int(used_bytes), int(current_bytes)
    step = int(policy["size_step_bytes"])
    options, refused, seen = [], [], set()
    for u in utilizations:
        u = float(u)
        if not 0 < u < 1:
            raise AnsibleFilterError("target utilisation %r must be between 0 and 1" % (u,))
        target = dsgrow_roundup(int(math.ceil(used / u)), step)
        if target in seen:
            continue
        seen.add(target)
        reasons = dsgrow_check_target(target, current, policy, array)
        if reasons:
            refused.append({"u": u, "target_bytes": target, "reasons": reasons})
            continue
        after = round(used * 100.0 / target, 1)
        options.append({
            "u": u,
            "target_bytes": target,
            "growth_bytes": target - current,
            "utilization_after_pct": after,
            "label": "Grow to %s (→ %s%%)" % (dsgrow_human_bytes(target), ("%g" % after)),
        })
    return {"options": options, "refused": refused}


# ---------------------------------------------------------------------------------------------- requests
def dsgrow_request_expired(expires_epoch, now_epoch):
    """True when the request's expiry (epoch seconds) is at or before now."""
    return int(now_epoch) >= int(expires_epoch)


def dsgrow_slack_escape(text):
    """Escape &, <, > for Slack mrkdwn (names from vCenter or VCF Ops end up in messages)."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def dsgrow_lock_name(vcenter, datastore):
    """File-system safe, stable, collision-free name for the per-datastore lock: a readable part (every other character
    becomes '_', cut to 120) plus a hash of the RAW names, so that 'ds 1' and 'ds_1' (or 'prod(a)' and 'prod_a_') do not
    share a lock. The vCenter name is case-insensitive, the datastore name is not. NOTE: changing this orphans every lock
    file that exists: only with nothing in flight (tests/unit/test_filter_dsgrow.py pins one value)."""
    vc, ds = str(vcenter).lower(), str(datastore)
    readable = re.sub(r"[^A-Za-z0-9._-]", "_", "%s__%s" % (vc, ds))[:120].lstrip(".")
    return "%s.%s" % (readable, hashlib.sha256(("%s\n%s" % (vc, ds)).encode("utf-8")).hexdigest()[:12])


class FilterModule(object):
    def filters(self):
        return {
            "dsgrow_is_everpure_device": dsgrow_is_everpure_device,
            "dsgrow_device_to_fa_serial": dsgrow_device_to_fa_serial,
            "dsgrow_fa_serial_to_device": dsgrow_fa_serial_to_device,
            "dsgrow_roundup": dsgrow_roundup,
            "dsgrow_human_bytes": dsgrow_human_bytes,
            "dsgrow_parse_size": dsgrow_parse_size,
            "dsgrow_check_target": dsgrow_check_target,
            "dsgrow_array_free_pct_after": dsgrow_array_free_pct_after,
            "dsgrow_size_options": dsgrow_size_options,
            "dsgrow_request_expired": dsgrow_request_expired,
            "dsgrow_lock_name": dsgrow_lock_name,
            "dsgrow_slack_escape": dsgrow_slack_escape,
        }
