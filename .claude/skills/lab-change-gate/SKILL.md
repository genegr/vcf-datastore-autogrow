---
name: lab-change-gate
description: Use before running anything against the lab or the run host - setup steps, probes, restarts, image builds, syncing - to follow the gated-change procedure (dry run, go-ahead, run, read-back, rerun) and the run-host tooling. Not for the demo phrases (skill dsgrow-demo).
---

# Gated lab changes (CLAUDE.md rule 10)

Real systems: vCenter, FlashArray, VCF Ops, the Slack workspace and the run host. Read-only probes and offline work
(lint, unit tests, mocks, the rulebook test) need no go-ahead. Everything else is one gated change at a time:

1. **Dry run**: `bash tools/setup.sh setup/<step>.yml` (forced `--check --diff`). Read the plan.
2. **Go-ahead**: the owner's, for that change only. Say what changes, the rollback and the read-back.
3. **Run**: `bash tools/setup.sh setup/<step>.yml --confirm <step>.yml`. Stop at the first failure and report.
4. **Read-back**: a probe or the dry run again, which must report `changed=0`.
5. Record it in `environments/<env>/docs/history.md` (what, when, log file, read-back).

Steps, owners and rollbacks: `setup/README.md`. Logs: `.state/logs/<playbook>-<UTC>[-check].log`.

## Where things run

- Edit on the Windows workstation. No heredocs or nested quoting there: write files, then copy or sync them.
- `bash tools/sync.sh` copies the tree to the run host (`SYNC_HOST` from the gitignored `.sync.env`). It never
  deletes: after removing or renaming a file, delete it on the run host too. It refuses while a datastore lock is
  proposed/executing/cleaning (`SYNC_FORCE=1` only with the owner's go-ahead). Without `SYNC_DIR` it writes the LIVE
  checkout the rulebook executes; for offline tests copy to a scratch directory (`SYNC_DIR=dsgrow-scratch bash
  tools/sync.sh`) and run the checks there, in containers (`bash tools/offline.sh`), touching no service.
- On the run host, in the repo, everything runs in the `dsgrow-eda:1` image. `.env` names the environment
  (`DSGROW_ENV` → `environments/<env>/inventory/`, passed as `dsgrow_env`) and the optional lab directory mounted at `/lab`:
  - `bash tools/probe.sh tests/probes/<probe>.yml`: read-only probes; `environments/<env>/probes/` holds one lab's own investigations.
  - `bash tools/offline.sh`: syntax and `tests/offline/logic.yml`, no network.
  - `docker compose run --rm -T eda ansible-lint [paths]`.
  - Services: `docker compose up -d rulebook bridge`. The rulebook mounts the repo (playbook changes apply on sync;
    rulebook changes need a restart). The bridge bakes its code into the image (rebuild after changes).
- Secrets: `.state/` (vault, env files, `vault-pass`), never in git. Addresses: the gitignored
  `environments/<env>/inventory/group_vars/dsgrow/site.yml` (`dsgrow_site`; template `environments/example/inventory/group_vars/dsgrow/site.yml.example`).

## Rules

- Never touch a request or the lock while an expansion is in flight; never approve on the owner's behalf.
- A restart or rebuild of a service is a run-host change: check nothing is in flight first (the lock file in
  `state/locks/`).
- After a failed run: report, wait. No manual fixes on the lab, no retries with changed parameters.
- The lab's own objects belong to whoever built it (vcf9-1: the nested-vcf repo); dsgrow changes only its own
  accounts, VCF Ops objects and the demo's objects.
