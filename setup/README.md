# Setting up dsgrow in an environment

Every step that changes a real system is a gate (CLAUDE.md rule 10, skill `lab-change-gate`):

1. A dry run: `bash tools/setup.sh <file>` forces `--check --diff`.
2. The go-ahead for that step only.
3. The real run: `bash tools/setup.sh <file> --confirm <file name>`.
4. A read-back, then a rerun that must report `changed=0`.

Logs go to `.state/logs/`. All commands run on the run host in the repo directory, after `bash tools/sync.sh` from
the workstation. Accounts are least-privilege: a `dsgrow` vCenter role and SSO user per vCenter, a `storage_admin`
API user per array. Admin credentials (from `lab.yml`) are used only by these setup steps and the demo.

| # | Step | Changes | Rollback |
|---|---|---|---|
| 0 | `.env` from `.env.example` (`DSGROW_ENV`, optional `DSGROW_LAB_DIR`); `inventory/<env>/` from `inventory/example/`; `inventory/<env>/group_vars/dsgrow/site.yml` from `inventory/example/group_vars/dsgrow/site.yml.example`; the admin secrets that `lab.yml` points to; `.state/vault-pass` (random, mode 0600) | files on the run host | delete them |
| 1 | `setup/secrets_init.yml` | `.state/secrets/eda.yml` (rulebook tokens, HMAC secret) | delete it |
| 2 | `setup/fa_user.yml` | `dsgrow` storage_admin user + API token on every array in `flasharrays.yml` | delete the user in Purity |
| 3 | `setup/vcenter_account.yml` (needs `govc` in the image: `DSGROW_IMAGE=<image with govc>`) | SSO user `dsgrow@<sso domain>` (password generated into `.state/secrets/vcenter-<key>.yml`), role `dsgrow` (Host.Config.Storage), permission on the datacenter, per vCenter | remove permission, role, user in the vSphere Client |
| 4 | Slack app from `slack/manifest.yml`, the bot invited to the channel, then `.state/secrets/slack.yml` | the Slack workspace | delete the app |
| 5 | `setup/deploy.yml`, then `docker compose build rulebook bridge && docker compose up -d rulebook bridge` | env files, two images, two containers on the run host | `docker compose down` |
| 6 | `setup/vcfops.yml` | two symptoms, one alert definition, the outbound webhook, its payload template and the notification rule in VCF Ops | `setup/vcfops_cleanup.yml` or the UI |
| 7 | `setup/template.yml` (demo) | the Ubuntu template from the OVA, in folder `dsgrow` | delete the template |
| 8 | `setup/vcfops_interval.yml` (demo, optional) | the demo vCenter's VCF Ops collection interval 5 → 1 min | the same step with `-e dsgrow_vcfops_interval_min=5` |
| - | the demo: `demo/init.yml`, `demo/start.yml`, `demo/cleanup.yml` | the demo's volume, datastore and VM | `demo/cleanup.yml` |

The secrets files and their keys are described in `setup/secrets.example.yml`. Create one by hand with:

```bash
ansible-vault create --vault-id dsgrow@.state/vault-pass .state/secrets/slack.yml
```

Read-back probes: `tests/probes/fa_identity.yml` (arrays), `tests/probes/ops_dsgrow_objects.yml` (VCF Ops objects),
`tests/probes/demo_status.yml` (the demo).

The images need about 2 GB on the run host (dsgrow-eda with Python, a JDK for ansible-rulebook and ansible-core; the
bridge about 150 MB).

## After a rebuild of the lab

Re-run steps 2, 3, 6, 7 and 8. Steps 1, 4 and 5 survive, because they live on the run host and in Slack.

How the reference lab `vcf9-1` was set up, step by step with its evidence: `docs/history.md`.
