---
name: eda-rulebook
description: Use when writing, changing or testing the ansible-rulebook that receives VCF Operations alerts and the Slack bridge's approval events - sources, conditions, throttling, run_playbook actions, the dsgrow-eda container and the local rulebook test.
---

# EDA rulebook (`rulebooks/datastore_capacity.yml`, plain ansible-rulebook, no AAP)

The rulebook is the visible hub of the demo (owner 2026-11-19). It never changes infrastructure itself.

| Rule | Source | Playbook | Writes? |
|---|---|---|---|
| propose | `vcfops` | `playbooks/propose.yml` | no (Slack + request file) |
| cleared | `vcfops` | `playbooks/cleared.yml` | no (Slack) |
| approved | `approvals` | `playbooks/execute.yml` | **yes**, the only writer |

Pins: ansible-rulebook 1.3.2, `ansible.eda` 2.13.0 (`docker/`).

## Sources

- `vcfops`: `ansible.eda.webhook` on `0.0.0.0:5080`, token. (Port 5000 belongs to another project on the run host.)
- `approvals`: `127.0.0.1:5081`, its own token plus `hmac_secret`, `hmac_header: x-dsgrow-signature`, hex. Only
  the bridge holds the secret.
- Secrets arrive as env vars from `.state/eda.env` (compose `env_file`).

## Conditions

- propose: source `vcfops`, `schema == "dsgrow.vcfops.v1"`, `event in ["new","updated"]`,
  `resource_kind == "Datastore"`, `alert_definition in vars.dsgrow_alert_definitions`, `alert_id is defined`.
  Throttle `once_within: 4 hours` grouped by `resource_id` + `alert_id` (owner 2026-11-21: every crossing of the
  limit is a new alert and gets a proposal; the same alert's "updated" events stay throttled). Overlap is refused by
  propose's per-datastore lock.
- cleared: source `vcfops`, same schema and kind, `event == "canceled"`.
- approved: `event.meta.source.name == "approvals"`, `schema == "dsgrow.approval.v1"`, `decision == "approve"`.
- A catch-all debug rule last (rules are first-match). An unmatched event is ignored, never an error.

## Facts (ansible.eda + ansible-rulebook source)

- The webhook event is `{"payload": <body>, "meta": {...}}`; the Authorization header is removed before queuing.
  ansible-rulebook adds `meta.source.name`, so a sender can't forge it.
- `run_playbook` copies only the playbook's directory: policy defaults live in `playbooks/group_vars/all/`, lab
  values in `environments/<env>/inventory/group_vars/dsgrow/`, roles and plugins via `ANSIBLE_CONFIG=/work/ansible.cfg`. The
  playbook gets `ansible_eda.event`, `.rule`, `.ruleset`; pass nothing else.
- `once_within` must be a literal "<n> <unit>"; keep it equal to `dsgrow_throttle`. Throttle state is in memory:
  a restart (`docker compose restart rulebook`, a run-host change) clears it.
- The container mounts the repo live: playbook changes apply on sync; rulebook changes need a restart.

## Rules for changes

- The `vcfops` source never reaches `execute.yml` (CLAUDE.md rule 11).
- Every new condition gets a fixture that matches and one that must not.

## Test (offline, no real systems)

`tests/rulebook/test_rulebook.py` runs ansible-rulebook against stub playbooks that record their calls and posts
every fixture to both sources. Expected: propose for the new alert, the N/A-symptoms alert and a new alert on the
same datastore after a cancel; nothing for updates of the same alert, a missing alert id, non-datastore, unknown
schema or an approval sent to `vcfops`; cleared once; execute once, from `approvals`, and a bad signature refused.

```bash
docker run --rm --network none -v "$PWD:/work" -w /work dsgrow-eda:1 python -m pytest -q -p no:cacheprovider tests/rulebook
```
(`--network none`: 5080/5081 are live on the run host.)
