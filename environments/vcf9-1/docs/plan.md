# dsgrow demo on the vcf9-1 lab: VCF Ops alert -> EDA -> Slack approval -> FlashArray + VMFS grow

> The original design plan (2026-11-19), kept for its reasoning. Some choices changed while building (for example
> every FlashArray call now goes through the everpure.flasharray modules). The current state is in README.md, CLAUDE.md
> and the skills; the history of every change is in environments/vcf9-1/docs/history.md.

## Context
The goal is a live demo on the nested VCF 9.1.1 lab (the nested-vcf repo, lab `vcf9-1`). When a VMFS datastore
fills too fast, VCF Operations raises an alert. An ansible-rulebook then posts a sizing proposal to a Slack channel.
An approver clicks a size, the rulebook runs the playbook that grows the Everpure FlashArray volume, and the VMFS
datastore is expanded. The repo has only the design (CLAUDE.md and 6 skills), no code yet.

Owner decisions (2026-11-19):
- Plain **ansible-rulebook**, no AAP.
- **Bridge -> rulebook**: the Slack bridge sends an authenticated "approved" event, and a rule runs `execute.yml`.
- Trigger: **used % >= 80 OR time remaining below N days**.
- Claude may change the lab **one gated change at a time**: dry run, go-ahead, rollback known, read-back, rerun changed=0.
- A **dedicated demo Slack workspace**, with approvers as an allow-list of user IDs.
- A **new dedicated datastore** `vcf9-1-w01-demo01`.
- **Least-privilege `dsgrow` accounts** on vCenter and on the arrays.

Facts from the lab that shape the design:
- **NVMe/TCP**: ESX names the device `eui.<32 hex NGUID>`, not `naa.624a9370…`. The lab records each NGUID in
  `.state/labs/vcf9-1/array-w01.json`. No eui->serial mapping exists yet. The expected Pure layout,
  `eui.00` + serial[0:14] + `24a937` + serial[14:24], is unverified.
- **everpure.flasharray 1.46.0 bug #1103**: `purefa_volume` on an existing volume and `purefa_info` with the volumes
  subset both fail with a bare "Module failed." (`nested-vcf/docs/upstream/everpure-flasharray-priority-adjustment.md`).
  Reads and the extend therefore go through REST 2.x via `ansible.builtin.uri`, following the login, `x-auth-token`
  and PATCH pattern in `nested-vcf/tests/ansible/array_volume_raw.yml` and `priority_adjustment_scratch.yml:96-104`.
  If a newer collection release fixes #1103, switch back to the modules.
- Targets:
  - vcenter-w01 `vcenter-w01.vcf9-1.lab`, datacenter `vcf9-1-w01-dc`, cluster `vcf9-1-w01-cl01`, esx04-06.
  - Array z-x90-a, host group `vcf9-1-w01`.
  - Ops `ops.vcf9-1.lab`, 9.1.1, token auth `POST /suite-api/api/auth/token/acquire`.
  - vcenter-w02 and z-x70-a are configured too, so the mapping is proven against two arrays.
- Run host (`dsgrow_site.run_host`): docker compose with `network_mode: host`, because the lab DNS sits in 172.17/16.
  Ports 53 and 8888 are already taken. The lab reaches it through the router's NAT. Internet egress works but Slack
  has not been tested yet.
- Pins to reuse (`nested-vcf/docker/requirements.*`): ansible-core 2.21.4, community.vmware 6.3.0,
  vmware.vmware 2.10.0, everpure.flasharray 1.46.0, py-pure-client 1.97.0, pyvmomi 9.1.1.0, community.general 13.4.0.

## Architecture (replaces the AAP flow in CLAUDE.md)
```
VCF Ops alert "dsgrow - datastore capacity" (used%>=80 OR time remaining < N d) on Datastore
  | Webhook Notification plugin, payload dsgrow.vcfops.v1 (new/updated/canceled), Bearer token
  v  http://<run host>:5080/endpoint
ansible-rulebook (container "dsgrow-eda", run host, host network)
  source vcfops    ansible.eda.webhook 0.0.0.0:5000, token
  source approvals ansible.eda.webhook 127.0.0.1:5001, token + hmac_secret (only the bridge knows it)
  rule propose  : schema/kind/definition match, throttle per resource_id + alert_id (owner 2026-11-21: every crossing gets a proposal) -> run_playbook playbooks/propose.yml
  rule cleared  : event canceled                                         -> run_playbook playbooks/cleared.yml
  rule approved : schema dsgrow.approval.v1 from source approvals        -> run_playbook playbooks/execute.yml
  catch-all debug
propose.yml (read-only): resolve -> preflight -> options -> Slack Block Kit post -> state/requests/<rid>.json
Slack bridge (container "dsgrow-bridge", Bolt, Socket Mode): ack, authz (allow-list), validate, disable buttons,
  POST HMAC-signed approval event to 127.0.0.1:5001; Deny just updates Slack
execute.yml (only writer): re-validate request file + expiry + approver + lock -> full preflight -> FA REST PATCH to
  absolute bytes -> rescan mounting hosts -> vmware_host_datastore auto_expand on one host -> refresh others -> verify
  -> Slack thread
```
- Request state lives in a mounted `state/` directory with one JSON file per request plus a per-datastore lock.
  This replaces AAP artifacts and covers the "one in flight" and expiry rules.
- `execute.yml` acts only on a `rid` that `propose.yml` wrote. It checks that the datastore, eui and vCenter in the
  event match the file, and checks the approver against the allow-list again, independently of the bridge.

## Step 1: update the design docs (no lab contact)
- `CLAUDE.md`:
  - Rewrite the flow and component table for ansible-rulebook with no AAP.
  - Rule 2: accept Everpure NVMe devices (`eui.` with the Pure layout) as well as `naa.624a9370`.
  - Rule 10: allow gated lab changes as described above.
  - The rulebook may run `execute.yml` only from the authenticated `approvals` source, never from a VCF Ops event.
  - Record the decisions above and remove them from "Open decisions".
  - Add the demo policy override.
- Skills:
  - `eda-rulebook`: two sources, `run_playbook` in place of `run_job_template`, the approved rule.
  - `slack-approval-bridge`: post to the rulebook instead of AAP; allow-list authz.
  - `flasharray-volume-extend`: REST path for #1103, eui mapping, `provisioned` PATCH.
  - `expansion-safety-review`: allow the approved rule, block execute from the vcfops source, accept eui.
  - `vmfs-datastore-expand`: eui device, accounts.

## Step 2: read-only probes (each a playbook in `tests/probes/`, run from the run host)
1. `eui_serial.yml`: read the NGUIDs in array-w01.json and array-w02.json and each volume's `serial` over REST `GET /volumes`. This proves the eui<->serial layout.
2. `ops_read.yml`: vCenter adapters for w01 and w02, Datastore resources, available stat keys (used %, time remaining), the Webhook Notification plugin type, and the API endpoints for payload templates, notification rules, alert and symptom definitions. Source: the appliance's `/suite-api/docs/rest/index.html`.
3. `runhost_read.yml`: df, free, `docker ps`, ports 5000/5001 free, HTTPS reachability of slack.com and wss-primary.slack.com.
4. Versions: the latest everpure.flasharray changelog (is #1103 fixed?), `ansible-doc community.vmware.vmware_host_datastore` (`auto_expand`), the extent and host fields of `vmware_datastore_info`, and the ansible-rulebook and ansible.eda releases to pin (webhook `hmac_*` options, `event.meta.source`, whether the `run_playbook` name can be templated).
5. vCenter privileges for rescan and VMFS expand, from the vSphere 9 docs. Measured in step 4.

## Step 3: code and offline tests
- `plugins/filter/dsgrow.py`: pure functions with unit tests in `tests/unit/`:
  - size options (round up to the step, dedupe, every cap)
  - naa<->serial and eui<->serial conversions (reject anything not from Everpure)
  - cap checks, binary units, request expiry
- Roles:
  - `dsgrow_resolve`: Ops payload -> vCenter, datastore, single VMFS6 extent, mounting hosts, then the array and volume found by serial across `dsgrow_flasharrays` (exactly one match).
  - `dsgrow_preflight`: all refusals.
  - `dsgrow_flasharray`: REST login, then `PATCH /api/2.x/volumes?names=<vol>` with `{"provisioned": <bytes>}` when current < target, skipped under check mode, read back so that size == target, logout; tokens under `no_log`.
  - `dsgrow_vmfs`: scanhba per host, auto_expand on the first host, refresh the others, verify.
  - `dsgrow_slack`: `uri` calls to chat.postMessage, chat.update and thread replies.
  - `dsgrow_state`: request file and lock.
- Playbooks `propose.yml`, `execute.yml`, `cleared.yml`: thin, each with block/rescue/always reporting to Slack, tags preflight, flasharray, vsphere and slack.
- Rulebook `rulebooks/datastore_capacity.yml` as in the architecture above.
- `bridge/`: `app.py`, `actions.py`, `authz.py` (allow-list), `validate.py`, `rulebook_client.py` (HMAC POST, httpx), `config.py`. Tests use mocked Slack and the rulebook, covering every case in the slack skill.
- Container images:
  - `docker/eda.Dockerfile`: ansible-rulebook (pinned, Java included), ansible.eda, the collections and Python packages pinned above.
  - `docker/bridge.Dockerfile`: Python 3.11+, slack_bolt.
- `compose.yaml` on host networking, plus `tools/sync.sh`, copied from the nested-vcf pattern.
- Secrets:
  - `.state/dsgrow.vault.yml` (gitignored) holds the Slack bot and app tokens, the EDA tokens, the HMAC secret, the dsgrow vCenter passwords and the dsgrow array tokens.
  - The bridge gets its secrets from an env file generated by a playbook from the vault, mode 0600 and never committed.
  - The nested-vcf vaults are read only, by the setup playbooks that need admin rights.
- `environments/vcf9-1/inventory/`: group_vars for vCenters (adapter_instance -> FQDN), arrays, the Slack channel, approver IDs.
  - Demo override: `dsgrow_size_step_bytes` 64 GiB. With a 512 GiB datastore at 80 %, the options come out at 640 and 704 GiB, both under the 50 % cap.
- `vcf-ops/payload-templates/` holds the three templates. `tests/fixtures/vcfops/` holds the six fixtures plus approval-event fixtures.
- `tests/test_rulebook.py`: runs the rulebook locally with the fixtures, using a test variant of the playbooks that only records each call. Expected: propose x1, the throttled `updated` x0, cleared x1, other events x0, an approval on the vcfops source x0, an approval on the approvals source x1.
- Gate for this step: yamllint, ansible-lint and pytest are clean, then the `expansion-safety-review` skill runs on the diff.

## Step 4: lab setup, one gate per change, each a playbook in `setup/`
1. **Demo datastore** (in the nested-vcf repo, by its own rules): add `{name: w01-demo01, size_gib: 512}` to the w01 `storage.datastores` in `labs/vcf9-1.yaml`, then run its create-only `wld_array` and `wld_datastore` phases. The NGUID goes into array-w01.json.
2. **Array user** `dsgrow` (storage_admin) and its API token on z-x90-a and z-x70-a: `setup/fa_user.yml` through REST, token stored in the dsgrow vault. Rollback is deleting the user, done by hand by the owner, since this repo never deletes.
3. **vCenter role** `dsgrow` (read, host storage partition config, datastore config) and the user `dsgrow@<sso domain>` on vcenter-w01 and vcenter-w02, with permission on the datacenter. Then measure: a `--check` run, followed by the real expand in step 7.
4. **Run-host deploy**: build both images, the compose project in `/home/ubuntu/containers/dsgrow/`, the env file. Then read back: the ports listen, the bridge connects to Slack.
5. **Slack app** (owner, by hand in the demo workspace): I supply `slack/manifest.yml` (Socket Mode, `chat:write`, interactivity). The owner installs it and puts the bot and app tokens, channel ID and approver IDs into the vault.
6. **VCF Ops** (`setup/vcfops.yml`, suite-api, re-appliable after each 90-day rebuild):
   - symptom definitions for used % >= 80 and time remaining < N days
   - the alert definition "dsgrow - datastore capacity" (either symptom) on the Datastore kind, enabled in the active policy
   - the Webhook Notification outbound instance pointing at the run host
   - the payload template (new, updated, canceled)
   - a notification rule scoped to that alert definition
   - read-back of every object. Endpoints are taken from the probe in step 2.2.

## Step 5: end-to-end demo run (gated, the first real write)
1. Fill `vcf9-1-w01-demo01` past 80 % with a lazy-zeroed thick VMDK on a throwaway VM, via a playbook in `demo/`. A thin FA volume costs almost no physical space for this.
2. Wait for the alert (Ops collects every 5 minutes) and check the Slack proposal: sizes, array headroom, buttons.
3. Approve a size, then check the thread updates. Then read back:
   - FA volume = target bytes
   - datastore capacity is on target on esx04-06
   - `state/` shows the request as done
   - rerun with the same rid: changed=0 (the FA step is a no-op)
4. Delete the filler VMDK, then check the canceled alert updates the original message through cleared.yml.
5. Negative runs:
   - a non-allow-listed clicker
   - an expired request
   - a custom size over the cap
   - a forged event on port 5000 with the approval schema, which must be ignored
6. Write the results and measurements (vCenter privileges, eui layout, timings) into the skills and a `environments/vcf9-1/docs/history.md`.
   The datastore only grows. Resetting it is a separate nested-vcf decision, outside this repo. At about 25 % per run
   there is room for many demo runs.

## Verification summary
- Offline: `yamllint .`, `ansible-lint`, `pytest tests/unit bridge/tests tests/test_rulebook.py`, the safety review.
- Lab: every gate's read-back as above. The real proof is the step 5 run: a Slack approval leads to a larger FA volume and VMFS datastore, with the approver named in the thread and in the state file.

## Critical files
- New in this repo: `CLAUDE.md`, `.claude/skills/*`, `rulebooks/datastore_capacity.yml`, `playbooks/{propose,execute,cleared}.yml`,
  `roles/dsgrow_*`, `plugins/filter/dsgrow.py`, `bridge/`, `docker/`, `compose.yaml`, `environments/vcf9-1/inventory/`, `setup/`,
  `demo/`, `vcf-ops/payload-templates/`, `slack/manifest.yml`, `tests/`.
- In nested-vcf, for step 4.1 only: `labs/vcf9-1.yaml`.
- Patterns reused from nested-vcf: `tests/ansible/array_volume_raw.yml` (REST login), `roles/lab_datastore/tasks/main.yml`
  (`vmware_host_datastore`, scanhba), `tests/ansible/vcf_ops_login.yml` (Ops token), `compose.yaml` and `tools/sync.sh`.

## Open questions

- ActiveCluster pod volumes or replicated volumes in scope? (Refused by preflight today.)
- The time-remaining trigger: VCF Ops 9.1.1 answers HTTP 500 for a symptom on `OnlineCapacityAnalytics|timeRemaining`;
  its shape and `dsgrow_time_remaining_days` for the demo are still open (skill vcf-ops-webhook).
- Run host disk: `/` was full on 2026-11-19; the host is shared, so the owner decides what Docker may prune.
