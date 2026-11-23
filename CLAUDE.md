# vcf-datastore-autogrow (internal name dsgrow): human-approved VMFS datastore expansion

When VCF Operations raises a capacity alert for a VMFS datastore backed by an Everpure FlashArray volume,
ansible-rulebook proposes sizes in Slack; an authorised admin clicks one; the rulebook runs `execute.yml`, which grows
the volume, rescans, expands the VMFS and reports in the Slack thread. This repo only grows datastores. Shrinking,
creating, deleting and migrating are out of scope. Architecture, components and setup: `README.md`.

Environments: everything specific to one lab lives in `environments/<env>/` (`inventory/`, `probes/`, `docs/`),
nothing outside it names a lab (`test_code_is_environment_neutral`, `test_lab_files_live_under_environments`). `.env`
selects one (`DSGROW_ENV`); `environments/example/` is the template; the README lists what a lab must provide.
Addresses go in the gitignored `inventory/group_vars/dsgrow/site.yml`, admin access in `lab.yml`. Reference lab:
`vcf9-1` (`environments/vcf9-1/README.md`), built by the separate nested-vcf repo, which owns its objects. Every
change to a lab is recorded in that environment's `docs/history.md`.

## Flow in one breath

VCF Ops alert → webhook `vcfops` source (5080) → `propose.yml` (read-only: resolve datastore → device → FlashArray
volume, preflight, size options, Slack message, request file + lock in `state/`) → admin clicks → Slack bridge
(Socket Mode: authz, bounds, signed event) → `approvals` source (127.0.0.1:5081, token + HMAC) → `execute.yml`, the
only writer (re-checks everything, grows the volume to absolute bytes, rescans, expands, verifies, reports).

## Safety rules (non-negotiable)

1. **Grow only**, to absolute target bytes. `execute.yml` asserts target > current; at target it does nothing.
2. **One datastore ↔ one extent ↔ one FlashArray volume.** Refuse non-VMFS 6, multi-extent, non-Everpure devices
   (`naa.624a9370…` / `eui.00…24a937…`), a serial on no array or on several.
3. **Caps** from `playbooks/group_vars/all/dsgrow.yml` (an inventory may tighten them or change the step). Over a
   cap: refuse with a reason in Slack. Never silently clamp a size the human chose.
4. **Execute re-runs the full preflight.** Only identifiers, the request id and the target size come from the
   event, and the identifiers must match the request file propose wrote.
5. **Requests expire** (4 h) and one request per datastore is in flight (the lock).
6. **Every change is attributable**: approver ID and name in the event, log, request file and thread. The approver
   is checked against `dsgrow_approver_ids` by the bridge and again by execute.
7. **No secrets or addresses in git.** Secrets: `.state/` on the run host, as env vars in the containers,
   `no_log: true`. Addresses: `environments/<env>/inventory/group_vars/dsgrow/site.yml` (gitignored, `dsgrow_site`).
8. **Nothing deletes, renames, disconnects, unmounts, resignatures or eradicates**, except `demo/`, which removes
   only its own objects after checking them against `.state/demo/state.json`. `tests/unit/test_repo_safety.py` and
   `tests/unit/test_rule8_inventory.py` check it (module allow-lists; no removal outside `demo/` but the one named rollback).
9. **Partial failure is safe**: volume grown, VMFS not = unused space; re-sending the approval finishes the job.
10. **Lab changes are gated, one at a time**: dry run, the owner's go-ahead for that change, rollback known,
    read-back, rerun `changed=0` (skill `lab-change-gate`). Read-only probes and offline work need none. The demo
    phrases "initialize the demo", "start the demo", "now clean up the environment" are standing go-aheads for one
    run each, without a dry run (skill `dsgrow-demo`). Unclear intent: ask; never assume cleanup.
11. **`execute.yml` runs only from the `approvals` source** with schema `dsgrow.approval.v1` (so does `deny.yml`, which
    records a denial and changes no infrastructure). The `vcfops` rules run only `propose.yml` and `cleared.yml`.
    Every field of an event that a playbook reads must pass the rulebook's allow-list first (Ansible renders event
    strings as templates); the tests find the fields by reading the playbooks.

Never approve on the owner's behalf. Never touch a request or the lock while an expansion is in flight.

## Conventions

- FQCN for every module; variables and roles prefixed `dsgrow_`. Bytes internally; GiB/TiB only in human text.
- Business logic (size math, device ↔ serial, caps) in `plugins/filter/dsgrow.py`, pure and unit-tested.
- Every FlashArray call through `everpure.flasharray` modules (patched for #1103), never direct REST.
- Rescans and storage refreshes only per the table in skill `vmfs-datastore-expand`.
- Each playbook: `block`/`rescue`/`always` reporting failures to the Slack thread; results in the request file.
- Contracts `dsgrow.vcfops.v1` and `dsgrow.approval.v1` change only together with fixtures, rulebook, templates,
  bridge and input validation.
- Edit on Windows (no heredocs, write files); everything Ansible runs on the run host in `dsgrow-eda:1`.
- Before committing a change to the execute path, bridge, rulebook, caps or rescans: skill `expansion-safety-review`.

## Commands

```bash
python -m pytest tests/unit bridge/tests          # workstation, pure Python
bash tools/sync.sh                                # copy to the run host (SYNC_HOST in .sync.env)
# run host, in the repo, with .env (DSGROW_ENV, DSGROW_LAB_DIR)
bash tools/offline.sh                             # syntax, logic, the real rulebook and the real playbooks against a fake world; no network
bash tools/probe.sh tests/probes/<probe>.yml      # read-only probes
bash tools/setup.sh setup/<step>.yml [--confirm <step>.yml]    # gated change (dry run without --confirm)
docker run --rm --network none -v "$PWD:/work" -w /work dsgrow-eda:1 python -m pytest -q -p no:cacheprovider tests/rulebook
docker compose run --rm -T eda ansible-lint
python3 tools/demo_follow.py                      # follow a demo expansion (read-only)
```

## Skills

| Skill | Use when |
|---|---|
| `dsgrow-demo` | The demo phrases; the demo lifecycle in `demo/` |
| `lab-change-gate` | Anything that runs against the lab or the run host |
| `eda-rulebook` | The rulebook, its sources, conditions, throttle and test |
| `vcf-ops-webhook` | VCF Ops symptoms, alert, webhook, templates, fixtures |
| `flasharray-volume-extend` | FlashArray mapping, preflight, extend, the #1103 patch |
| `vmfs-datastore-expand` | vCenter lookup, rescan rules, VMFS expand, verify |
| `slack-approval-bridge` | Slack message, buttons, bridge, approval event |
| `expansion-safety-review` | Before committing changes to the execute path, bridge, rulebook, caps or rescans |
