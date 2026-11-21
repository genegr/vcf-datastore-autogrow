# Setting up the dsgrow demo on lab vcf9-1

Every step that changes a real system is a gate (CLAUDE.md rule 10). Each gate has four parts:

1. A dry run: `bash tools/setup.sh <file>` forces `--check --diff`.
2. The owner's go-ahead for that step only.
3. The real run: `bash tools/setup.sh <file> --confirm <file name>`.
4. A read-back, then a rerun that must report `changed=0`.

Logs go to `.state/logs/`. All commands run on the run host in `~/vsphere-dsgrow`, after `tools/sync.sh` from the
workstation. Accounts are least-privilege: a `dsgrow` vCenter role and SSO user per vCenter, a `storage_admin` API user
per array.

| # | Step | Changes | Who | Rollback |
|---|---|---|---|---|
| 0 | `.state/vault-pass` (random, mode 0600); `inventory/<env>/group_vars/dsgrow/site.yml` from `inventory/site.example.yml` (the addresses, not in git) | run host / workstation files | Claude or owner | delete them |
| 1 | `setup/secrets_init.yml` | `.state/secrets/eda.yml` | Claude | delete it |
| 2 | (done 2026-11-19, retired by step 11) the first demo datastore `w02-demo01` (512 GiB, z-x70-a) in nested-vcf `labs/vcf9-1.yaml`, then nested-vcf `--tags wld_array,wld_datastore -e wld=w02` | array volume + VMFS (create-only) | nested-vcf gates | step 11 |
| 3 | `setup/fa_user.yml` | `dsgrow` storage_admin user + API token on z-fastaines-d (key z-x90-a), z-fastaines-f (key z-x70-a) | Claude | delete the user in Purity |
| 4-5 | `setup/vcenter_account.yml` | SSO user `dsgrow@<sso domain>` (govc `sso.user.create`, password generated into `.state/secrets/vcenter-<key>.yml`), role `dsgrow` (Host.Config.Storage), permission on the datacenter; on vcenter-w01 and vcenter-w02 | Claude | remove permission, role, user in the vSphere Client |
| 6 | Slack app from `slack/manifest.yml`, bot invited to the channel, then `.state/secrets/slack.yml` | demo Slack workspace | **owner, by hand** | delete the app |
| 7 | `setup/deploy.yml`, then `docker compose build rulebook bridge && docker compose up -d rulebook bridge` | env files, 2 images, 2 containers on the run host | Claude | `docker compose down` |
| 8 | `setup/vcfops.yml` | 2 symptoms, 1 alert, 1 outbound instance, 1 template, 1 rule in VCF Ops | Claude | delete them in the UI |
| 9 | (done 2026-11-20, retired) the first demo: a filler VM on `vcf9-1-w02-demo01`, approved in Slack | **the expansion** 512 → 640 GiB | Claude + the approver | grow-only: no undo (CLAUDE.md) |
| 10 | `docker compose build rulebook` (dsgrow-eda:1 with `everpure.flasharray` 1.46.0 + the #1103 patch, pycdlib, ssh), then `docker compose up -d rulebook` | image + container on the run host | Claude | the previous image, tagged `dsgrow-eda:1-pre-fa` first |
| 11 | `demo/retire_demo01.yml`, then nested-vcf's model by hand: `w02-demo01` out of `labs/vcf9-1.yaml` and `.state/labs/vcf9-1/array-w02.json` (`volumes`, `nguid`; backup first), proof: nested-vcf `--check --tags wld_array,wld_datastore -e wld=w02` | filler VM, datastore and volume `vcf9-1-w02-demo01` removed (eradicated) | Claude | none after the eradication (owner's choice) |
| 12 | `setup/template.yml` | template `ubuntu-srv-2604-tpl` on `vcf9-1-w02-ds01`, folder `dsgrow` | Claude | delete the template |
| 13 | `setup/vcfops_interval.yml` | collection interval of VCF Ops adapter instance `vcenter-w02.vcf9-1.lab`: 5 → 1 min (the demo's alert within a minute) | Claude | the same step with `-e dsgrow_vcfops_interval_min=5` |
| - | the demo: "initialize the demo" / "start the demo" / "now clean up the environment" (skill dsgrow-demo) | `demo/init.yml`, `start.yml`, `cleanup.yml` | Claude, on the owner's phrase | `demo/cleanup.yml` |

The secrets files and their keys are described in `setup/secrets.example.yml`. Create one by hand with:

```bash
ansible-vault create --vault-id dsgrow@.state/vault-pass .state/secrets/slack.yml
```

## Before step 7

The run host's `/` was 100 % full on 2026-11-19 (1.7 GB free). The eda image needs about 1.5 GB (Python 3.12, JDK 17,
ansible-core, ansible-rulebook) and the bridge image about 150 MB. `docker system df` showed about 26 GB of reclaimable
images and 17 GB of build cache. The run host is shared with other projects, so the owner decides what to prune.

## After the 90-day rebuild of the lab

Re-run steps 3-5, 8, 12 and 13. Steps 1, 6, 7 and 10 survive a rebuild, because they live on the run host and in Slack.
