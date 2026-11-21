#!/usr/bin/env python3
"""Follow one demo expansion after "start the demo", so the owner doesn't have to report the click (owner 2026-11-20).

READ-ONLY: reads the request files in state/requests/ and the bridge's log. Never approves, denies or writes.
Run on the run host, in ~/vsphere-dsgrow, right after demo/start.yml:

    python3 tools/demo_follow.py [--since 2026-11-20T21:27:00] [--minutes 60] [--datastore dsgrow-demo01]

--since defaults to the newest .state/logs/start-<UTC>.log. Prints one line per step (proposal posted, approved, done)
and exits when the request ends:
    0 done   1 failed, or refused before its first write   2 denied, cleared or expired before a click
    3 nothing within --minutes
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time

GIB = 1024 ** 3


def gib(n) -> str:
    return "%g GiB" % round(int(n) / GIB, 2)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def utc(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(str(text).rstrip("Z").split("+")[0])


def default_since() -> dt.datetime:
    logs = sorted(f for f in glob.glob(".state/logs/start-*.log") if not f.endswith("-check.log"))
    if not logs:
        return now_utc() - dt.timedelta(minutes=10)
    m = re.search(r"start-(\d{8}T\d{6})Z", logs[-1])
    return dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")


def say(text: str) -> None:
    print("%s  %s" % (now_utc().strftime("%H:%M:%S"), text), flush=True)


def latest_request(ds: str, since: dt.datetime):
    found = []
    for path in glob.glob("state/requests/*.json"):
        try:
            with open(path, encoding="utf-8") as fh:
                req = json.load(fh)
        except (OSError, ValueError):
            continue          # being written: next round
        if req.get("ds") == ds and utc(req.get("created_at") or "1970-01-01") >= since:
            found.append(req)
    return max(found, key=lambda r: r["created_at"]) if found else None


def bridge_log(container: str) -> str:
    try:
        out = subprocess.run(["docker", "logs", "--since", "4h", container], capture_output=True, text=True,
                             timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout + out.stderr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=utc, default=None)
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--datastore", default="dsgrow-demo01")
    ap.add_argument("--bridge", default="vsphere-dsgrow-bridge-1")
    ap.add_argument("--interval", type=float, default=5)
    a = ap.parse_args()
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    since = a.since or default_since()
    deadline = time.time() + a.minutes * 60
    say("following %s: requests created since %s UTC, for %d min" % (a.datastore, since.isoformat(), a.minutes))

    seen, sent_at = set(), 0.0
    while time.time() < deadline:
        req = latest_request(a.datastore, since)
        if req:
            rid, status = req["rid"], req.get("status")
            if "proposed" not in seen:
                seen.add("proposed")
                at = req.get("at_proposal") or {}
                used = (100.0 * at["vmfs_used_bytes"] / at["vmfs_capacity_bytes"]) if at.get("vmfs_capacity_bytes") else 0
                refused = ["%s (%s)" % (gib(r["target_bytes"]), "; ".join(r.get("reasons", []))) for r in req.get("refused") or []]
                say("PROPOSED %s at %s UTC: datastore %.1f %% used; options %s%s" % (
                    rid, req["created_at"][:19], used, ", ".join(gib(o) for o in req.get("options") or []) or "none",
                    ("; refused " + ", ".join(refused)) if refused else ""))
            appr = req.get("approval") or {}
            if appr and "approved" not in seen:
                seen.add("approved")
                say("APPROVED by %s (%s) for %s at %s" % (appr.get("approver_name"), appr.get("approver_id"),
                                                         gib(appr.get("target_bytes", 0)), appr.get("received_at", "")[:19]))
            if status == "done":
                r = req.get("result") or {}
                took = ""
                if appr.get("received_at") and r.get("finished_at"):
                    took = ", %d s after the click" % (utc(r["finished_at"]) - utc(appr["received_at"])).total_seconds()
                say("DONE %s: volume %s -> %s, VMFS %s -> %s on %s%s" % (
                    rid, gib(r.get("volume_before_bytes", 0)), gib(r.get("volume_after_bytes", 0)),
                    gib(r.get("vmfs_before_bytes", 0)), gib(r.get("vmfs_after_bytes", 0)),
                    ", ".join(r.get("hosts") or []), took))
                return 0
            if status == "failed":
                say("FAILED %s: %s" % (rid, str(req.get("failure") or req.get("result") or "?")[:500]))
                return 1
            if status == "cleared" and not appr:
                say("CLEARED %s: the alert was canceled before a click" % rid)
                return 2
            if status == "proposed" and not appr:
                log = bridge_log(a.bridge)
                if ("request %s denied" % rid) in log:
                    say("DENIED %s in Slack" % rid)
                    return 2
                # execute refused before its first write: the request stays proposed, only the thread gets a note
                sent = re.search(r"^(\S+ \S+),\d+ .*approval %s sent: (.*)$" % re.escape(rid), log, re.M)
                if sent:
                    if "sent" not in seen:
                        seen.add("sent")
                        sent_at = time.time()
                        say("CLICKED (bridge): %s" % sent.group(2))
                    elif time.time() - sent_at > 180:
                        say("REFUSED? %s: approval sent, but execute did not start within 3 min (see the Slack "
                            "thread and the rulebook log)" % rid)
                        return 1
                if int(req.get("expires") or 0) and time.time() > int(req["expires"]):
                    say("EXPIRED %s without a click" % rid)
                    return 2
        time.sleep(a.interval)
    say("nothing more within %d min (last: %s)" % (a.minutes, "no proposal" if "proposed" not in seen else
                                                    "waiting for the click" if "approved" not in seen else "executing"))
    return 3


if __name__ == "__main__":
    sys.exit(main())
