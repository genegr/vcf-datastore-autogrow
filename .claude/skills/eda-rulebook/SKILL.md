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
| denied | `approvals` | `playbooks/deny.yml` | request file only (status `denied`, who, when); no infrastructure |

Pins: ansible-rulebook 1.3.2, `ansible.eda` 2.13.0 (`docker/`).

## Sources

- `vcfops`: `ansible.eda.webhook` on `DSGROW_EDA_VCFOPS_BIND` (the run host's `.env` through compose.yaml, default
  `0.0.0.0`; never a tracked file, sync would overwrite it):5080, token, plain HTTP. (Port 5000 belongs to another project on the run host.)
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
- denied: the same source and schema, `decision == "deny"` (the bridge sends one for every Deny click: a deny must not live
  only in the bridge's memory, or a Custom form opened before it could still approve).
- **Every field a playbook reads is on an allow-list.** ansible-core renders the strings of an event (they reach the
  playbook as extra vars) as Jinja when a playbook reads them, so `{{ lookup('pipe', ...) }}` in a `resource_name` would run
  in the container that holds every credential. Each playbook rule therefore carries an `is match("^...$")` for every
  payload field the playbooks and roles READ (and `meta.endpoint`, the sender's URL path): strict character sets for the
  identifiers (alert_id, resource_id, resource_name, adapter_instance, which is optional: `is not defined or is match`),
  and for the text a playbook shows in Slack (criticality, cancel_time, link) a pattern that refuses only a Jinja opener
  (`{{`, `{%`, `{#`) and a leading `#` (a `#jinja2:` header): a lone brace, an unresolved `${PLACEHOLDER}` or a link that is
  not a URL is real data. Fields no playbook reads (status, adapter_kind, the times, symptoms), `sent_at`, an extra key and
  `meta.headers` have NO pattern, so a value VCF Operations formats in an unexpected way can never drop an alert (the first
  real alert on 2026-11-23 was dropped by a pattern that was too strict). They stay inert because nothing renders the event
  as a whole (`test_no_playbook_renders_an_event_as_a_whole`; `tests/offline/test_flow.py` runs the real playbooks with
  template text in each, with a positive control). The tests find the fields by reading the playbooks: one that starts
  reading a new field fails `test_every_field_a_playbook_reads_from_an_alert_has_a_pattern` until the rulebook has a pattern.
  An alert for our definition that fails a pattern falls through to the "Rejected alert" rule, which LOGS the values it saw
  (`dsgrow REJECTED a vcfops event ...`): read that line first when a real alert produces no proposal. Pattern rules: plain
  classes, no backslash, lookaround or doubled brace (rulebook engine = Java matcher; `{{` in a string is rendered by
  ansible-rulebook itself). The playbooks' own regexes run after the rendering and guard nothing.
- The bridge signs only what the approvals rule accepts: `approver_name` goes through `validate.clean_name`
  (`[A-Za-z0-9 ._()@-]`, 80 characters, anything else `_`).
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
- A strict field needs TWO conditions: `is match("^...$")` and `is not search("[^ -~]")`. The engine's `$` also matches in
  front of a final line terminator (tests/rulebook posted "id" + newline and propose started); the second condition refuses
  any character outside printable ASCII. `tests/unit/test_rulebook_allowlist.py` fails for a strict field without it.
- `meta.headers`, `payload.sent_at` and an extra key reach the playbook in the event with no pattern. ansible-core 2.21.4 (read
  in its `_lazy_containers.py`) renders a value of the event when that item is read, but a whole-container operation
  (`to_json`, `str()`, `combine`, a loop over the values, `debug var=`) renders every value in it: no task may dump, print or
  loop over `ansible_eda.event`, `.meta` or `.meta.headers` (nor `dsgrow_alert`, `dsgrow_approval`, `dsgrow_denial`) as a
  whole. Keep ansible-core pinned (docker/requirements.txt).

## Test (offline, no real systems)

`tests/rulebook/test_rulebook.py` runs ansible-rulebook against stub playbooks that record their calls and posts
every fixture to both sources. Expected: propose for the new alert, the N/A-symptoms alert and a new alert on the
same datastore after a cancel; nothing for updates of the same alert, a missing alert id, non-datastore, unknown
schema or an approval sent to `vcfops`; cleared once; execute once, from `approvals`, and a bad signature refused.

```bash
docker run --rm --network none -v "$PWD:/work" -w /work dsgrow-eda:1 python -m pytest -q -p no:cacheprovider tests/rulebook
```
`tools/offline.sh` runs it under the rulebook service's own settings (code read-only, `.state` hidden, no capability) and
then `tools/flow_tests.sh`: the real propose / execute / deny / cleared playbooks against a fake FlashArray, vCenter and
Slack (`tests/offline/test_flow.py`). Template text in every alert and approval field is posted to the real rulebook by `tests/rulebook/` and checked against
the patterns by `tests/unit/test_rulebook_allowlist.py`; the flow tests cover the lifecycle, not the injection.
(`--network none`: 5080/5081 are live on the run host.)
