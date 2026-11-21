---
name: expansion-safety-review
description: Use before committing any change to playbooks/execute.yml or the roles it uses, the Slack bridge, rulebook conditions, policy caps, credentials, rescans or the VCF Ops payload contract. Reviews the change against the project safety rules and blocks on violations.
---

# Expansion safety review

Review the diff, not the whole repo. Report each finding as **BLOCK**, **WARN** or **OK** with file and line. Any
BLOCK means the change isn't ready. Never run anything against real systems as part of the review.

## Static checks (the diff and every file it pulls into the execute path)

| Pattern | Severity |
|---|---|
| `state: absent`, `eradicate`, `destroy`, `rename`, `move`, `overwrite`, `resignature` outside `demo/` | BLOCK |
| `purestorage.flasharray.*`, or any direct FlashArray REST (`uri` to an array) | BLOCK |
| A size passed as a delta instead of absolute bytes | BLOCK |
| A size or metric from the payload, the approval event or Slack used for a write (only identifiers, rid and target bytes may come from the event) | BLOCK |
| A secret literal, or a task handling a secret without `no_log: true` | BLOCK |
| A rule running `execute.yml` without source `approvals` + `dsgrow.approval.v1`; `approvals` not on 127.0.0.1 or without token + HMAC | BLOCK |
| Bridge code sending anywhere but the approvals URL, or another schema | BLOCK |
| `ignore_errors: true` or `failed_when: false` on a write task | BLOCK |
| An address (IP, URL) committed instead of `dsgrow_site` | BLOCK |
| Module without FQCN; write task outside `block`/`rescue` that reports to Slack | WARN |
| Caps loosened in `playbooks/group_vars/all/dsgrow.yml` or an inventory | WARN, say what changed |
| A rescan or storage refresh outside the table in skill vmfs-datastore-expand | WARN |

## Behaviour checks (read the code path)

1. `execute.yml` re-runs the full preflight before the first write.
2. It asserts target > current, skips the FlashArray step at target, and every cap holds.
3. Mapping refuses multi-extent, non-VMFS 6, non-Everpure devices and ambiguous matches.
4. Expiry, request-file match, approver allow-list and the per-datastore lock are checked before any write.
5. The approver is in the event, the request file and the Slack thread.
6. A failure after the FlashArray step leaves a safe state, and a re-run completes it.
7. The rescan covers every mounting host; the expand host is asserted to mount the datastore right before
   `vmware_host_datastore`.

## Tests

`pytest tests/unit bridge/tests` and the rulebook test pass, new logic has tests, `ansible-lint` is clean on the
changed files. A payload or rulebook change re-runs the rulebook test against every fixture.

## Output

```
Safety review: <BLOCK | PASS with warnings | PASS>
- BLOCK playbooks/execute.yml:42 extend task reads size from dsgrow_alert
- WARN  playbooks/group_vars/all/dsgrow.yml:4 max growth raised 50% -> 80%
- OK    preflight re-run before first write
Not verifiable offline: <list>
```
