# dsgrow journal

Dated measurements and decisions; newest last. Skills hold the lasting facts, this file the evidence.

## 2026-11-19: design, code, read-only probes

Owner's decisions (the plan is `docs/plan.md`):
- plain ansible-rulebook with no AAP; approvals come back through the rulebook
- trigger on used ≥ 80 % OR time remaining
- a dedicated demo Slack workspace
- a new demo datastore `vcf9-1-w01-demo01`
- least-privilege accounts
- gated lab changes

Upstream code read for the design:
- `ansible.eda` webhook (main): token and `hmac_*` options; the Authorization header is popped before the event is
  queued.
- ansible-rulebook v1.3.2: `insert_meta_info` sets `meta.source.name`; `run_playbook` copies only the playbook's
  directory; a directory inventory is copied whole; `execution_strategy` defaults to sequential; `throttle.once_within`
  is pattern-checked.
- community.vmware 6.3.0: `vmware_host_datastore` with `auto_expand` uses `ExpandVmfsDatastore(options[0].spec)` and
  honours check mode. With `state: present` it creates a VMFS if the datastore isn't mounted on the host.
  `vmware_host_scanhba` has no check mode.

Read-only probes on the lab (run host, nested-vcf toolbox image, outputs in `.state/probes/`):
- `tests/probes/eui_serial.yml`:
  - The NVMe device ID is `eui.00` + serial[0:14] + `24a937` + serial[14:24]. It was proven on all 4 lab volumes
    (vcf9-1-mgmt-ds01, w01-ds01, w01-dr01 on z-x90-a; w02-ds01 on z-x70-a), both directions.
  - REST 2.57-2.59 on both arrays. `GET /volumes?filter=serial='…'` returns exactly that volume.
  - `/arrays/space` has `total_used` and `total_physical`. Capacity is 26.8 TiB on z-x90-a and 49.8 TiB on z-x70-a.
- `tests/probes/backing_read.yml`: `plugins/modules/dsgrow_datastore_backing` works against vCenter 9.1.1. For
  w01-ds01 it reports VMFS 6, one `eui.` extent, esx04-06 mounted and accessible, and 2 TiB device size on every host
  (= the volume).
- `tests/probes/ops_read.yml` and `ops_api_docs.yml` (VCF Ops 9.1.1):
  - Endpoints: `/api/alertplugins` (type `WebhookPlugin`, config `Url`), `/api/notifications/templates`,
    `/api/notifications/rules` (none exist yet), alert and symptom definitions, policies.
  - Datastore metrics: `capacity|usedSpacePct` and `OnlineCapacityAnalytics|timeRemaining` (366 on an idle
    datastore).
  - The vCenter adapters are named by FQDN.
  - **There is no `${ADAPTER_INSTANCE}` placeholder.** The playbooks find the vCenter by searching for the datastore.
  - Request models come from the appliance's REST docs.
- Run host: 127.0.0.1:5000 is taken by another project (`python3`), so we use 5080/5081. `/` is 100 % full (1.7 GB
  free). That blocks the image build until the owner decides what to prune.

- Slack from the run host: `POST https://slack.com/api/api.test` returned 200 in 0.45 s, and `wss-primary.slack.com`
  completed TLS. Socket Mode can connect outbound.

Offline checks:
- Workstation: 134 pytest tests (filters, bridge with a Bolt dispatch smoke test, static safety checks); yamllint is
  clean.
- Run host, `tools/offline.sh` with `--network none`: every playbook passes syntax-check, and
  `tests/offline/logic.yml` passes (76 ok, 4 intended refusals). It covers the options for 512 GiB at 80 % (640 and
  704 GiB), the proposal blocks, the state and lock round trip, the status blocks and the execute caps.
  `tests/offline/test_proposal.py` showed every rendered button parses in the bridge.
- Bugs found by these checks and fixed:
  - `x-auth-token` session calls weren't `no_log`.
  - `rejectattr('block_id')` failed on blocks without one (ansible-core 2.21), which would have broken `execute.yml`
    and `cleared.yml`.
  - A flow-list YAML error in `setup/tasks/secret_write.yml`.
- Not run yet: the rulebook test (`tests/rulebook/`) and ansible-lint, which both need the dsgrow-eda image (run host
  disk).

## 2026-11-19 (later): run host cleanup, setup steps 0-5, first live read path

Owner's go-ahead: clean up the run host disk; create the users; proceed with the setup steps.

**Run host disk.** A workflow audited every Docker image against the sibling projects on the workstation, one agent
per project, and an adversarial check per removal candidate. None of the 15 candidates was refuted.

- Removed:
  - nested-vcf-toolbox `:1-pre-{vapi,dns,vcsa,everpure146,7zip}` (rollback tags; nested-vcf's docs keep only
    `:1-pre-pexpect`)
  - `vmw2ntnx-probe:1`
  - `python:3.12-slim`
  - quay.io/ansible `default-test-container` v2.22-7, v2.22-8, v2.23-2 and `ansible-test-utility-container` 2.0.0,
    3.1.0, 3.2.0, 3.4.0, 3.5.0 (vsphere-ansible's `ansible-test --docker` re-pulls them)
  - the build cache (36.6 GB)
- Kept:
  - the current toolboxes
  - `nested-vcf-toolbox:1-pre-pexpect` (the running depot container still uses that image id)
  - `coredns:1.15.0`, `vmw2ntnx:dev`, `node:22.16.0-alpine`, `fedora:44`, `python:3.13-slim`, `vyos-build`
  - the unclaimed `wordpress`, `mariadb`, `hello-world` images and their volumes (no project claims them; left for
    the owner)
- `/` went from 100 % (1.7 GB free) to 76 % (48 GB free). The depot (:8888 on the host IP), lab DNS and vmw2ntnx are
  still up.

**w01 found degraded (left as found).** `vcf9-1-w01-ds01` and `vcf9-1-w01-dr01` are **unmounted on esx04 and esx05**
and mounted only on esx06. vCenter agrees. A datastore `vcf9-1-dstest01` (1 TiB, device
`eui.006c1b16ce1c034d24a9371c06495c13`, created 2026-11-10 10:08) is mounted on all three hosts and is not in the lab
model. Evidence: `tests/probes/esx_vmfs_read.yml` (esxcli storage filesystem list) and `tests/probes/backing_read.yml`.
`QueryVmfsDatastoreExpandOptions` on esx04 answers `vim.fault.NotFound` for both datastores, because they are not
mounted there. On w02 it answers 0 options with no fault. Consequences:
- nested-vcf's `lab_datastore` cannot rerun on w01 (its dry run failed on `w01-ds01`).
- dsgrow's preflight would refuse w01's datastores ("hosts not mounted/accessible"), as designed.

**Step 2: demo datastore on w02, not w01.** nested-vcf `labs/vcf9-1.yaml` now has w02 `{name: w02-demo01, size_gib:
512}`. Run: `lab.yml -e lab=vcf9-1 -e wld=w02 --tags wld_array,wld_datastore`.
- Dry run clean; real run rc 0.
- Volume `vcf9-1-w02-demo01` on z-x70-a, device `eui.00274318935cf84e24a9372b02e1e7ec`, connected to host group
  `vcf9-1-w02`.
- VMFS 6 created from esx07; esx08 and esx09 rescanned; "every host has every datastore" passes.
- The rerun's only changes were nested-vcf's layer-check evidence files.
- Logs: nested-vcf `.state/logs/dsgrow-demo01-w02-*.log`.

**Step 0-1:** `.state/vault-pass` and `.state/secrets/eda.yml` (vault id `dsgrow`); the rerun reported `changed=0`.

**Step 3: `setup/fa_user.yml`.** User `dsgrow` with role storage_admin and an API token on z-x90-a and z-x70-a.
- everpure.flasharray 1.46.0 `purefa_user` returns the token as `user_info` (a string), not the
  `user_info.user_api` of its examples. The first run therefore lost the issued token (never logged: `no_log`).
- The fixed run rotated it (`api: true` on an existing user deletes the old token and issues a new one) and stored
  it.
- Read-back: the stored token logs in as `dsgrow` on both arrays. The rerun reported `changed=0`.
- Leak check: no stored secret appears in any log.

**Steps 4-5: `setup/vcenter_account.yml`.**
- `dsgrow@w01.everpure.demo` and `dsgrow@w02.everpure.demo` were created with `govc sso.user.create` (no module
  exists; guarded by `govc sso.user.ls`), each password generated and stored first.
- Role `dsgrow` (Host.Config.Storage), with a propagating permission on each datacenter.
- Both users log in. The rerun reported `changed=0`.

**First live read path as the dsgrow accounts** (`tests/probes/resolve_read.yml`, read-only) on `vcf9-1-w02-demo01`:
- The vCenter was found by searching both configured vCenters, then the backing was read (esx07-09).
- `eui.00274318935cf84e24a9372b02e1e7ec` -> serial `274318935CF84E2B02E1E7EC` -> z-x70-a / `vcf9-1-w02-demo01`.
- Volume 512 GiB, VMFS 511.75 GiB, 0.3 % used, array 51.5 % free. No option offered (correct: grow only).
- Live data found one bug: REST 2.59 answers `pod: {name: null}` for a volume outside a pod, and the preflight's
  `default('')` did not catch the null. Fixed, and the offline test now uses the real shape.

**Images and checks.**
- Built `dsgrow-eda:1` (897 MB) and `dsgrow-bridge:1` (195 MB). `.dockerignore` keeps `.state/` out of the build
  context.
- All with `--network none`:
  - pytest: 135 passed, including the **rulebook test against ansible-rulebook 1.3.2 + ansible.eda 2.13.0**. It
    checks the 401s for a bad token and a bad HMAC, propose x2, the throttled update, cleared x1, execute x1 only from
    the signed approvals source, and that an approval body sent to the vcfops port is ignored.
  - ansible-lint (production profile): passed with 0 failures and 0 warnings.
  - `tools/offline.sh`: passed.
- Fixed along the way:
  - ansible-core 2.21 rejects a dict used as a `when` condition (setup/vcfops.yml).
  - The module's RETURN docs and doc fragment are now `vmware.vmware.base_options`.

**Step 8 dry run (`setup/vcfops.yml`):** clean. VCF Ops has none of the dsgrow objects yet.

## 2026-11-20: w01 repaired, dstest01 removed, demo services up, VCF Ops wired

**w01** (nested-vcf `playbooks/w01_dr_repair.yml`; its journal has the details):
- ds01/dr01 are mounted again on esx04-06. VOMA can't check NVMe/TCP volumes (no reservation); the substitute
  checks passed (`vmkfstools -x check` error free on every disk, clean VMFS logs, clean guest kernel logs).
- The VMs came back with the same UUIDs.
- `vcf9-1-dstest01` was removed: HA preference set to ds01/dr01, unmount + detach on every host, array disconnect,
  destroy (recoverable for 24 h), device entries forgotten. Every rerun reported changed=0.

**Bridge.**
- Bolt `App(token=...)` refuses to start without a signing secret, because of its HTTP request-signature
  middleware. With Socket Mode there are no inbound requests, so `request_verification_enabled=False`
  (`bridge/app.py build_app`, with a test that builds it the way production does).
- The bridge connects ("Bolt app is running").

**Smoke test through the real path**, a synthetic VCF Ops `new` then `canceled` for `vcf9-1-w02-demo01`:
- propose: 80 tasks ok as the dsgrow accounts, the Slack post (no option: 0.3 % used), the request file and the
  lock
- cleared: the Slack message withdrawn
- One bug found: in `cleared.yml` a block's `when` on `dsgrow_request.status` was re-evaluated after the status
  changed, which skipped the request write and the lock release. It now decides once (`dsgrow_status_at_clear`).

**VCF Ops** (`setup/vcfops.yml`). Created: symptom "dsgrow - datastore used 80 pct or more", alert "dsgrow -
datastore capacity", WebhookPlugin "dsgrow-eda" (enabled), template "dsgrow.vcfops.v1", rule "dsgrow - datastore
capacity to EDA". The rerun reported changed=0. Lessons are in skill vcf-ops-webhook:
- HTML-escaped names created 7 duplicate symptoms, cleaned up by `setup/vcfops_cleanup.yml`.
- Alert definitions created through the API are enabled in no policy.
- The condition value must be a string.
- The time-remaining symptom answers 500 and is off for now.

**Demo.** `demo/fill.yml` took `vcf9-1-w02-demo01` to 85.1 % (filler VM with a 434 GB thick disk); VCF Ops read
85.08 %. The alert definition was enabled in "Default Policy" at 12:44Z.

**First real expansion, 2026-11-20 (end to end):**
- 12:48Z: VCF Ops raised "dsgrow - datastore capacity" on `vcf9-1-w02-demo01` (85.08 %). The rulebook ran propose,
  which posted options of 640 / 704 / 768 GiB.
- 12:53Z: the approval (the owner, 640 GiB) arrived, and execute failed before any write. The cause:
  `vars: dsgrow_fa_match: "{{ dsgrow_fa_match }}"` loops ("Recursive loop detected"). The rescue marked the
  request failed, released the lock and offered the buttons again, as designed. Fixed. New test
  `test_no_self_referencing_variables` covers self-references under `vars:`.
- Before the second approval, the write path was dry-run against the lab: execute.yml `--check` with the real
  approval, 103 ok, 0 failed.
- 13:08Z, second approval: z-x70-a volume 512 → 640 GiB; VMFS 511.75 → 639.75 GiB on esx07-09 (mounted,
  accessible, same capacity everywhere); 68.1 % used; request done, lock released. VCF Ops cancelled the alert by
  itself, and cleared.yml posted the note in the thread.
- Read back independently: `tests/probes/resolve_read.yml`, `backing_read.yml`.
- Next demo: `demo/unfill.yml`, then `demo/fill.yml` (fill refills to 85 % of the new 640 GiB).

## 2026-11-20 (afternoon) - FlashArray through the modules; the demo lifecycle

**Owner's decisions.**
- The demo resets to nothing each time: `demo/init.yml`, `start.yml` and `cleanup.yml`, plus a template made once
  (`setup/template.yml`).
- The cleanup eradicates the demo volume at once.
- The phrases work by intent, not by exact wording (skill dsgrow-demo).
- FlashArray calls go through the `everpure.flasharray` modules only, never direct REST. For #1103, the image patches
  1.46.0.

**#1103 status (read 2026-11-20).**
- The issue is closed upstream (fix commit 333dcca), but 1.46.0 is still the latest release, without it.
- The unguarded reads of `priority_adjustment_value` in 1.46.0: purefa_info 1910, 2007, 2844, 2950; purefa_volume
  412, 1008, 1211-1222; purefa_vg 522, 534.
- `docker/patches/everpure-flasharray-1.46.0-issue1103.patch` was generated from those files (11 reads become
  `getattr(..., None)`).
- `docker/check_flasharray_patch.py` feeds `generate_vol_dict` and `_volfact` a null priority. On stock 1.46.0 both
  raise AttributeError (measured in a throwaway container). Patched, both pass. A second apply is refused.

**Gate 1, the dsgrow-eda image (step 10).**
- Old image tagged `dsgrow-eda:1-pre-fa` (d1c582dbf212) as the rollback.
- `docker compose build rulebook`: the patch applied and the build check passed (log
  `.state/logs/build-eda-20261120.log`). The new image is f46e13d5e0e1, 1.86 GB.
- Offline checks in the new image:
  - syntax of every playbook: ok
  - `tests/offline/logic.yml`: 76 ok, 0 failed, now on the 200 GiB demo numbers (256/272/288 GiB)
  - unit, bridge and offline pytest: 140 passed
  - ansible-lint (production) on playbooks, roles, setup, demo: 0 failures. The w01-repair investigation probes
    still carry 40-odd old lint findings.
- The rulebook test failed with 401 because the live rulebook holds 5080/5081 on the host network. It now refuses
  early when the ports are taken. Run in `docker run --network none` (CLAUDE.md Commands), it passes.
- Read-only on the lab, `tests/probes/resolve_read.yml` through the patched `purefa_info`:
  - both arrays answer in ~10 s each (purefa_info volumes reads connections per volume)
  - the serial matches exactly one volume, z-x70-a / vcf9-1-w02-demo01, 640 GiB
  - array free 51.48 %
- `execute.yml --check` with the last real request and a 656 GiB target: the new `purefa_volume` extend reported
  `changed` (640 -> 656) and wrote nothing; 80 ok, 0 failed. Read back: the state files are unchanged since 13:08 and
  the volume is still 640 GiB.
- `docker compose up -d rulebook`: recreated on f46e13d5e0e1. Both sources answer 401 to a wrong token. The rerun:
  "Running", nothing changed.

**Independent review before gate 2** (workflow, 5 reviewers + 5 skeptics; all 15 findings confirmed, 0 refuted;
fixed):
- **Blockers that would have stopped every demo run:**
  - init, cleanup and retire built their problem lists in folded scalars with a line break before `{{ out }}`, so
    the result was the string " []" and `| length == 0` never held. Now `{{- out }}`, with
    `test_problem_lists_render_as_lists`.
  - The guest host lacked `ansible_connection: ssh`, so with `-c local` the guest plays would have run inside the
    container.
- **Safety:**
  - extend: `purefa_volume` addresses by name and CREATES (or recovers from the trash) when no live volume has it.
    Now the volume is re-read on that array right before the write (serial and size must match) and the serial the
    module reports is checked right after; the module's own sub-second window remains.
  - Cleanup now closes open requests and takes the lock as `cleaning` BEFORE removing anything. execute.yml refuses
    a lock held by another rid; lock_read counts `cleaning` as held.
  - close_requests wrote requests back unchanged (an include var outranks set_fact): it now writes them directly
    and reads them back.
  - A trashed volume's serial is checked before an eradicate.
  - retire: purefa_info `volumes[].hosts` lists the members of a connected host group, so private connections are
    now taken from `hosts[].volumes` without a host group.
  - The forget step is gated on the record, not on this run's changes, so a partial failure is finished by a rerun.
- **Resumability:**
  - init records the VMFS UUID right after the create and the VM UUID right after the clone (the clone also carries
    the annotation `dsgrow-demo <instance>`).
  - init and cleanup accept their own half-built objects by the EUI recorded before the create, or by that
    annotation.
- Also caught by the review: `vars: dd_new: >- {{ dd_new | ... }}` would loop, as on the first approval.
  `test_no_self_referencing_variables_multiline` now catches folded values.
- start: ext4 metadata is written at once (no lazy init). The fill size is measured after the format, from a
  refreshed datastore.
- **HA, found on w02:** `allFeasibleDsWithUserPreference`, no preferred datastore. cleanup and retire now record it,
  use `userSelectedDs [vcf9-1-w02-ds01]` only for the removal, and put it back exactly, with a read-back. init no
  longer touches HA.

**Gate 2 dry runs (15:2xZ):**
- `demo/retire_demo01.yml --check`: identity ok (filler, datastore VMFS 6aff7eb0-7219263f-25a5-005056320700 on
  eui.00274318935cf84e24a9372b02e1e7ec, serial 274318935CF84E2B02E1E7EC, only host group vcf9-1-w02); 23 ok,
  6 changes, 0 failed.
- nested-vcf baseline `lab.yml -e lab=vcf9-1 -e wld=w02 --tags wld_array,wld_datastore --check`: 0 failed (its
  changes are evidence files only).

**Gate 2, retire `vcf9-1-w02-demo01` (owner's go-ahead, 15:20Z)** (`.state/logs/retire_demo01-20261120T152002Z.log`):
- The run: 32 ok, 9 changed, 0 failed.
  - filler VM deleted
  - HA userSelectedDs [ds01]; HA let go of the datastore
  - VMFS 6aff7eb0-... unmounted and eui.0027...e7ec detached on esx07-09
  - volume disconnected from host group vcf9-1-w02, destroyed and eradicated on z-x70-a
  - rescan; detached-device entries forgotten
  - HA put back as found (allFeasibleDsWithUserPreference, preferred []; vSphere cleared the list)
- Read back by the playbook: no datastore, the volume neither live nor in the trash. Record
  `.state/demo/retire-demo01.json`.
- Rerun: changed=0 ("removing: nothing (done)").
- nested-vcf model:
  - `labs/vcf9-1.yaml`: the w02-demo01 entry was replaced by a comment. Only that file was copied to the run host,
    after its sha256 matched the Windows copy.
  - `.state/labs/vcf9-1/array-w02.json`: vcf9-1-w02-demo01 dropped from `volumes` and `nguid` (backup
    `array-w02.json.pre-retire-demo01-20261120`).
- nested-vcf `lab.yml -e lab=vcf9-1 -e wld=w02 --tags wld_array,wld_datastore --check`: 0 failed, changed=6, the extra
  one being its local `lab.json` re-rendered once from the new lab file (check_mode false by design). A second run:
  changed=5, the same as the baseline.

**Gate 3, the template (owner's go-ahead, 15:40Z)** (`.state/logs/template-20261120T153955Z.log`):
- Folder `/vcf9-1-w02-dc/vm/dsgrow` created; `dsgrow-ubuntu-2604-tpl` deployed from the stripped descriptor (pinned
  OVA, sha256 checked) on vcf9-1-w02-ds01, thin, powered off, and marked as a template (instance UUID
  5027aa5c-53e9-b8ab-145c-94184739d436).
- The run then failed at its read-back assert: every condition held, but `fail_msg: "{{ tp_after.instance }}"` is a
  dict, and ansible-core 2.21 validates the message before the conditions (the 2026-11-20 lesson, repeated). The same
  shape was fixed in init.yml and start.yml; `test_assert_messages_are_strings` now catches it.
- Rerun = read-back: changed=0, "dsgrow-ubuntu-2604-tpl: template in /vcf9-1-w02-dc/vm/dsgrow on ['vcf9-1-w02-ds01']".

**First demo init (owner: "please initialize the demo", 15:49Z)** (`.state/logs/init-20261120T154908Z.log`):
- Dry run clean ("ok: nothing exists yet"). The real run made the volume (serial 274318935CF84E2B02E2FAF1, private
  connections to esx07-09), VMFS 6 `dsgrow-demo01` (6b006c35-ec02310b-5f34-005056320700) and the clone
  `dsgrow-demo-vm` (5027fe8d-ca22-d9c5-59ab-20213fe18e35), then failed at "SSH through the router" after 601 s:
  `kex_exchange_identification: Connection closed by remote host`.
- Read-only diagnosis (`tests/probes/demo_guest_net.yml`, SSH hop tests): the router is fine, but its ARP for the demo VM's address
  on eth1.1206 is FAILED. The NIC is connected on pg-app (VLAN 1206) and the router sees the guest's IPv6 link-local,
  yet Tools reports hostname `ubuntu` and no IPv4: cloud-init never got the seed. The seed ISO is on the datastore,
  but the VM's only CD-ROM is the template's, IDE 1:0, "Remote device", not connected; nothing on IDE 0.
- Cause: `community.vmware.vmware_guest` `configure_cdrom()` returns early when its vm_obj is a template, and on a
  clone vm_obj is the template, so the clone's `cdrom:` spec was silently dropped.
- Fix in `demo/init.yml`: clone powered off without a cdrom spec, then put the seed on the clone's IDE 1:0 CD-ROM
  (while still off: IDE has no hot-add), power on, and assert that a connected CD-ROM holds the seed before any SSH
  wait. Lint, unit tests and the dry run are clean. The VM already made stays unseeded: rerunning init on it fails
  at the new assert; it needs cleanup, then init.

**Demo reset (owner: "reset the demo", 16:40Z)**:
- The cleanup dry run failed at "No expansion is executing": `fail_msg` read `dsgrow_lock.rid` while `dsgrow_lock` was
  `{}` (no lock), and ansible-core 2.21 resolves the message before the condition. Fixed with `| default('?')`; the
  second dry run listed exactly the recorded objects. Real run after the owner's go-ahead for the edited playbook
  (`.state/logs/cleanup-20261120T164521Z.log`): "the demo environment is clean", volume eradicated (not in the
  trash), lock `released`, HA back to allFeasibleDsWithUserPreference.
- Init with the CD-ROM fix (`.state/logs/init-20261120T164908Z.log`): the seed assert, SSH, cloud-init and the guest
  check passed; VCF Ops resource e5be4be5-3bc2-4aa5-a6f2-3cc844f15590; Slack "demo ready". Record: serial
  274318935CF84E2B02E31B89, VMFS 6b007a44-d940d847-1e84-005056320700, VM 50278387-ba48-43ec-9729-64620d5abe6a.
  Read-back: dsgrow-demo01 1.8 % used of 199.75 GiB, volume 200 GiB.

**Demo start, then the fill made fast (owner: "start the demo", 16:58Z; then "the disk must be filled in a minute")**:
- `demo/start.yml` (`.state/logs/start-20261120T165821Z.log`): thin 180 GiB disk, ext4, `dsgrow-fill` writing
  165824 MiB; about 8 GiB/min, ~20 min to 80 %. The owner: unusable in a demo, not even 5 minutes.
- Cleanup at the owner's request ("clean up everything", `.state/logs/cleanup-20261120T170856Z.log`): clean, volume
  eradicated, lock released.
- `demo/start.yml` rewritten: one THICK lazy-zeroed disk sized to `capacity * fill_pct / 100 - used`, hot-added
  (VMFS allocates it at once, nothing is written), storage refreshed, asserted >= 80 %, Slack, then ext4 (lazy init,
  nodiscard). `data_disk_gib` and the `dsgrow-fill` unit are gone. Not yet run on the lab.
- `tests/probes/ops_collection.yml`: all VMWARE adapter instances collect every 5 min; and VCF Ops kept reporting
  5.89 % during the fill (two collections), because guest writes never refresh vCenter's datastore summary: the old
  fill would not have alerted in time anyway. Next: a gated change of vcenter-w02's `monitoringInterval` to 1.

**Gate 13, the collection interval (owner's go-ahead, 17:23Z)** (`setup/vcfops_interval.yml`):
- Dry run: one change, `PUT /api/adapters` id cef3eb7c-f032-428e-a2b3-77daf09e129e (vcenter-w02.vcf9-1.lab),
  monitoringInterval 5 -> 1, everything else as read.
- Real run (`.state/logs/vcfops_interval-20261120T172312Z.log`): HTTP 200; read back interval 1, resourceKey,
  collectorId 2 and credential unchanged. Rerun (`...T172322Z.log`): changed=0, "nothing to do: already 1 min".
- Rollback: the same step with `-e dsgrow_vcfops_interval_min=5`.
- Proof (`tests/probes/ops_collection.yml`, 17:28Z): vcf9-1-w02-ds01 samples every 60 s from 17:23:59 (300 s before);
  vcenter-w01 and the management vCenter still at 5 min.

**Demo init with the fast collection (owner: "please initialize the demo", 17:25Z)**:
- Built and checked by 17:27:42 (volume serial 274318935CF84E2B02E323FE, VMFS 6b0082be-c6209c7b-da00-005056320700,
  VM 502775bf-51a5-3a27-cb62-2bf26708ae2b). VCF Ops discovered the datastore at 17:26:41 (resource a6388aff-...), but
  init's wait required exactly one resource NAMED dsgrow-demo01, and VCF Ops keeps the two removed demo datastores
  under that name (NOT_EXISTING, grey): it could never succeed after the first reset.
- Fix (the owner: match the object id, not the name): `dsgrow_datastore_backing` also returns `moid` and
  `vcenter_uuid`; init records `datastore_moid` + `vcenter_uuid`; `demo/tasks/vcfops_wait.yml` lists every VMWARE
  Datastore resource and takes the one with VMEntityObjectID = moid and VMEntityVCID = the vCenter UUID (VCF Ops' own
  uniqueness identifiers), STARTED. Proven read-only by `tests/probes/demo_vcfops_wait.yml`.
- On the owner's word the stuck run was stopped (`docker stop`) and init rerun (`.state/logs/init-20261120T174657Z.log`,
  91 s): resumed, "VCF Ops resource a6388aff-... = dsgrow-demo01 (datastore-35, vCenter 60b239ed-...)", Slack
  "demo ready". Read-back: dsgrow-demo01 3.9 % of 199.75 GiB, volume 200 GiB, lock released.

**First fast start (owner: "start the demo", 17:50Z)** (`.state/logs/start-20261120T175016Z.log`):
- Dry run: "thick data disk to add: 161.94 GiB to reach 85 %". Real run: the disk was added (thick, lazy-zeroed,
  `[dsgrow-demo01] dsgrow-demo-vm/dsgrow-demo-vm_1.vmdk`), then the run failed at its own check: "dsgrow-demo01 is at
  3.9 % after the disk". vCenter read 85.0 % seconds later (`tests/probes/demo_disk.yml`): start.yml had refreshed only
  esx07 and read at once, while the VM runs on esx08. No Slack "Demo started", /data not formatted.
- The product ran anyway: rulebook propose 17:51:42, Slack proposal 17:52:09 (options 256 / 272 / 288 GiB, request
  811fdece-db87-4e2d-875a-c5c6c75c37a5, lock proposed): 1 min 53 s from "start the demo" to the proposal.
- Fix in `demo/start.yml`: refresh every host after the disk, re-read until >= 80 % (12 x 5 s), and the "Demo started"
  note once per demo (`started` in the record), so a rerun after a failure still sends it. Lint and tests clean.
- Approval 17:54:17 by the owner (Slack member ID redacted): the bridge sent 274877906944 (256 GiB), the value of the
  first (primary) button; the owner said 272 GiB. Labels and values come from the same option
  (`dsgrow_size_options`), and the bridge forwards the clicked button's `tgt` unchanged. Resolved: the owner confirmed
  clicking the 256 GiB button; click, event and result agree.
- Execute (request 811fdece-...): done 17:55:11, 0 failed. Volume 200 -> 256 GiB on z-x70-a, VMFS 199.75 -> 255.75 GiB
  on esx07-09. Read-back: dsgrow-demo01 66.4 % used (169.79 GiB of 255.75 GiB), lock released. 61 s from the click to
  done; 5 min 3 s from "start the demo" to done.
- Cleanup (owner: "now clean up the environment", `.state/logs/cleanup-20261120T175738Z.log`): "the demo environment
  is clean"; VM, datastore and the 256 GiB volume removed (eradicated), HA back to allFeasibleDsWithUserPreference,
  lock released. Read-back: datastore and volume absent. vcenter-w02 stays at 1-minute collection (step 13).

**Demo volume to the host group; arrays by their Purity names (owner, 2026-11-20 evening)** (offline + read-only):
- Owner: connect the demo volume to the hosts' host group, found by the hosts' NQNs; per host object only when they
  are in no host group. `tests/probes/esx_nqn.yml`: vCenter has no host NQN (only the array's target NQN); on the array
  vcf9-1-esx07/08/09 carry nqn.2014-08.lab.vcf9-1:nvme:esx07..09 and form host group vcf9-1-w02 (one volume,
  vcf9-1-w02-ds01). Owner's choices: read the NQN with esxcli over SSH; accept that nested-vcf's wld_array read-back
  (the group provisions exactly its model's sizes) fails while the demo volume exists.
- `demo/tasks/attach_target.yml`: vmk0 address from vCenter (the router's DNS has no lab zone: NXDOMAIN), `esxcli nvme
  info get` as root through the router with nested-vcf's esx_ecdsa key, NQN -> exactly one host object each -> one
  host group with exactly these hosts (purefa_hg, only `volume`) or none (per host) or refuse. Init records `attach`;
  cleanup checks the volume is connected only as recorded and disconnects that way (purefa_hg state absent always with
  the volume: without it purefa_hg deletes the group). Proven read-only by `tests/probes/demo_attach.yml`:
  "host group vcf9-1-w02 (exactly vcf9-1-esx07, vcf9-1-esx08, vcf9-1-esx09)".
- Owner: "replace z-x70-a with z-fastaines-f". `tests/probes/fa_identity.yml`: z-x70-a IS z-fastaines-f (FA-X70R3,
  Purity 6.12.4) and z-x90-a is z-fastaines-d (FA-X50R3) - a rename. Owner's choices: both arrays,
  visible name only. `dsgrow_flasharrays[].name` = the Purity name; new `key` = the old alias, which names the vault
  variable, the secret file and nested-vcf's token map, so no secret, env file or container changes. Proof:
  setup/deploy.yml --check leaves eda.env (the FA tokens) unchanged; the probes authenticate on both arrays.
- Found on the way, not fixed (gated): `.state/bridge.env` (12:15Z) gives the bridge a 64 GiB size step, the policy
  says 16 GiB; only the bridge's check of a Custom size uses it.
- Checks: 146 unit + bridge tests, tests/offline, ansible-lint on 65 files; init --check "nothing exists yet",
  cleanup --check "clean". Not yet run for real: the host group connect and disconnect.

**Bridge step fixed, then init (owner: "fix the bridge step, then initialize the demo", 18:32Z)**:
- `setup/deploy.yml` (dry run: only bridge.env would change; backup `.state/bridge.env.pre-step-20261120`): bridge.env
  DSGROW_SIZE_STEP_BYTES 68719476736 -> 17179869184, eda.env unchanged; `docker compose up -d --force-recreate
  --no-deps bridge`; the bridge logs caps size_step_bytes=17179869184 and reconnected to Slack; rerun changed=0.
- Init (`.state/logs/init-20261120T183342Z.log`, 159 s): "z-fastaines-f: connect dsgrow-demo01 to host group
  vcf9-1-w02" changed, "Connected where intended, and nowhere else" ok, VCF Ops found datastore-38, Slack "demo ready".
  Read-back: host group vcf9-1-w02 now holds vcf9-1-w02-ds01 (nsid 841) and dsgrow-demo01 (nsid 199), hosts unchanged;
  record `attach` {mode: hgroup, hgroup: vcf9-1-w02}; datastore 1.8 % of 199.75 GiB; volume 200 GiB, serial
  274318935CF84E2B02E334A2.

**Start with the fixed start.yml (owner: "start the demo", 18:39Z)** (`.state/logs/start-20261120T183913Z.log`):
- Dry run "thick data disk to add: 166.25 GiB" (vCenter's figure before a refresh); the real run refreshed first and
  added 161.93 GiB, thick. "dsgrow-demo01 at 85.0 % 41 s after the start", Slack "Demo started", /data formatted,
  0 failed, 58 s in all.
- Propose 18:40:45, Slack proposal 18:41:10 (256 / 272 / 288 GiB on z-fastaines-f, request
  e3232a57-17ac-4fa4-b0a6-3499a4467e31): 1 min 57 s from "start the demo" to the proposal.
- The owner said "approved 272 GiB"; the bridge sent 274877906944 (256 GiB) at 18:43:01 by <owner>, the second
  time in a row. The stored proposal is right: dsgrow_approve_1 "Grow to 256 GiB" tgt 274877906944,
  dsgrow_approve_2 "Grow to 272 GiB" tgt 292057776128, dsgrow_approve_3 "Grow to 288 GiB" tgt 309237645312. The bridge
  forwards body.actions[0].value (the clicked button); it logs neither action_id nor label, so the logs cannot tell a
  click on button 1 from a Slack-side mix-up. Open.
- Execute done 18:43:53: volume 200 -> 256 GiB, VMFS 199.75 -> 255.75 GiB on esx07-09; read-back 66.4 % used, lock
  released.

**Bridge: the clicked button made unambiguous (owner: "make the bridge changes", 19:15Z)**:
- `bridge/actions.py`: the click handler passes the clicked button's action_id and label; a label that shows a size
  must show human(target) (text compare: labels round to 2 decimals), else nothing is sent and the clicker is told;
  the log line and the thread reply carry the button id and label. The event itself is unchanged.
- `roles/dsgrow_slack/templates/proposal.json.j2`: no size button styled primary; each has Slack's confirm dialog
  naming datastore, current size, target, growth and array.
- Tests: 4 new bridge tests (mismatch refused, rounding accepted, logged/quoted, no-size label), Bolt wiring with a
  label, tests/offline/test_proposal.py checks confirm limits, no style and label == value. 150 unit + bridge, the
  rulebook test, the offline render, ansible-lint, yamllint clean. Safety review: PASS.
- Deploy: `docker tag dsgrow-bridge:1 dsgrow-bridge:1-pre-confirm` (rollback), `docker compose build bridge`,
  `docker compose up -d --force-recreate --no-deps bridge`: Bolt running, the new code in /app. The template is live
  for the next propose (the rulebook mounts the repo). Not yet seen in Slack.

**Cleanup with the host group (owner: "now clean up the environment", 19:17Z)**
(`.state/logs/cleanup-20261120T191750Z.log`): the identity check accepted host group vcf9-1-w02 as recorded;
"z-fastaines-f: disconnect dsgrow-demo01 from host group vcf9-1-w02" changed (per-host step skipped), destroy +
eradicate, HA back to allFeasibleDsWithUserPreference, "the demo environment is clean". Read-back: datastore and
volume absent, lock released; host group vcf9-1-w02 back to exactly its hosts esx07-09 and its one volume
vcf9-1-w02-ds01 (nsid 841), so nested-vcf's wld_array model holds again.

**Init (owner: "please initialize the demo", 19:22Z)** (`.state/logs/init-20261120T192241Z.log`, 156 s): connected to
host group vcf9-1-w02 on z-fastaines-f, VCF Ops found the datastore, Slack "demo ready", 0 failed. Read-back:
dsgrow-demo01 1.8 % of 199.75 GiB, volume 200 GiB, lock released.

**Start with the new buttons (owner: "start the demo", 19:27Z)** (`.state/logs/start-20261120T192708Z.log`):
- "dsgrow-demo01 at 87.2 % 41 s after the start" (disk 166.25 GiB thick), 0 failed. Propose 19:28:47, Slack proposal
  19:29:14 (request 9f04419b-ca49-47dc-8cf1-02c9e625836a): 2 min 6 s. Buttons: "Grow to 256 GiB (→ 68%)" and "Grow to
  272 GiB (→ 64%)", no style, each with its confirm dialog ("Grow to 256 GiB?" / "Grow to 256 GiB", same for 272).
- Only two options: 87.2 % instead of 85 % (sized from a stale 1.8 %: the pre-measure refresh touched esx07 only), so
  288 GiB fell over the 50 % cap. Fixed offline: start.yml refreshes every host before measuring too.
- The owner said "approved 272 GiB". The new bridge log, from Slack's own click payload: "approval 9f04419b-... sent:
  ... -> 274877906944 bytes (256 GiB) by <owner>, button dsgrow_approve_1 'Grow to 256 GiB (→ 68%)'" at 19:29:41,
  i.e. Slack reported the first button, after its confirm dialog "Grow to 256 GiB?". The label/value check passed
  (they agree). Execute done 19:30:36: volume 200 -> 256 GiB, VMFS 199.75 -> 255.75 GiB; read-back 68.1 % of
  255.75 GiB, lock released.
- Cleanup (owner: "now clean up the environment", `.state/logs/cleanup-20261120T193349Z.log`): disconnected from
  host group vcf9-1-w02, destroyed + eradicated, HA restored, "the demo environment is clean". Read-back: datastore and
  volume absent, lock released, vcf9-1-w02 back to esx07-09 and vcf9-1-w02-ds01 only.

## 2026-11-20: one-click approve, no demo dry runs (owner)

- Owner: "remove the intermediate step in which you wait for my confirmation for the expansion". The size buttons'
  Slack confirm dialog is gone (`roles/dsgrow_slack/templates/proposal.json.j2`); one click approves. Kept: no
  default style, the bridge's label/value check, the action_id + label in the log and the label quoted in the thread.
  `tests/offline/test_proposal.py` now asserts no `confirm`. No bridge rebuild: the template is read by propose.yml
  in the rulebook container, which mounts the repo.
- Owner: "skip all the dry run executions during the demo". CLAUDE.md rule 10 and skill dsgrow-demo: the demo phrases
  run their playbook with `--confirm` directly. Each demo playbook asserts its preflight before its first change;
  `tools/setup.sh` still forces `--check` without `--confirm`, so a dry run stays available on request.

- Init (owner: "please initialize the demo", first run without a dry run, `.state/logs/init-20261120T212336Z.log`):
  ok=68 changed=13 failed=0, "demo ready". Read-back: dsgrow-demo01 199.75 GiB at 1.8 %, volume 200 GiB on
  z-fastaines-f connected to host group vcf9-1-w02, VM dsgrow-demo-vm present, lock released.
- Start (owner: "start the demo", no dry run): 87.2 % after 48 s (166.25 GiB thick disk sized from 3.54 GiB / 1.8 %,
  even with every host refreshed before measuring: ~4.4 GiB not in vCenter's figure, suspected the VM's 4 GiB swap file,
  not yet probed). Proposal 21:29:20 (request 99a80b1e-...): 256 / 272 GiB, one-click buttons, no confirm dialog.
- The owner approved 272 GiB; the bridge log agrees: "button dsgrow_approve_2 'Grow to 272 GiB (→ 64%)'" at 21:29:41.
  Execute done 21:30:37 (56 s after the click), ok=99 changed=8 failed=0. Read-back: volume 272 GiB, dsgrow-demo01
  64.1 % of 271.75 GiB, request done, lock released.
- Cleanup (owner: "now clean up the environment", no dry run, `.state/logs/cleanup-20261120T213356Z.log`): ok=63
  changed=11 failed=0, "the demo environment is clean", about 2 min. Read-back: VM, datastore and volume absent, record
  gone, lock released; host group vcf9-1-w02 still exactly esx07-09.
- Owner: "what i want to remove is your checkpoint" (my "click a size and tell me; I'll follow the execute"), not the
  Slack dialog (I had misread the first request and removed that). New `tools/demo_follow.py` (read-only: request files
  + bridge log): after start.yml it follows proposal -> click -> done/failed/denied/cleared/expired; skill dsgrow-demo
  runs it in the background after every start and reports the outcome without waiting for the owner. Checked against
  request 99a80b1e: PROPOSED 87.2 % (256/272, 304 refused), APPROVED 272 GiB, DONE 54 s after the click, exit 0.
- Owner: "put the confirmation dialog back". proposal.json.j2 and test_proposal.py restored to the confirm dialog per
  size button ("Grow to X?" / "Grow to X" / "Cancel"); no default style, the bridge's label/value check unchanged.

## 2026-11-21: init phrase

- Owner: the init phrase is now just "initialize the demo" (was "please initialize the demo"): CLAUDE.md rule 10 and
  skills table, skill dsgrow-demo, setup/README.md, demo/init.yml header, cleanup.yml's final message.
- Init (owner: "initialize the demo", `.state/logs/init-20261121T010149Z.log`): ok=68 changed=13 failed=0, about
  2 min 40 s. Read-back: dsgrow-demo01 199.75 GiB at 1.8 %, volume 200 GiB on z-fastaines-f in host group vcf9-1-w02,
  VM dsgrow-demo-vm, lock released.
- Start (owner: "start the demo"): 85.0 % after 44 s (161.94 GiB disk: this time the measurement already counted the
  ~4.3 GiB that was missing on 2026-11-20). Followed by tools/demo_follow.py, no checkpoint: proposal 01:07:34 (request
  a9478cb8-...) with 256 / 272 / 288 GiB; click 01:07:50, bridge "button dsgrow_approve_1 'Grow to 256 GiB (→ 66.3%)'";
  done 01:08:46 (53 s after the click): volume 200 -> 256 GiB, VMFS 199.75 -> 255.75 GiB; read-back 66.4 %, lock released.
- Owner: "a weird rescan of the hbas at 1:06, when the virtual disk has been added". Cause: vmware_host_scanhba's
  rescan_hba defaults to true, so start.yml's two storage refreshes (before measuring, after the disk) ran RescanAllHba
  on esx07-09 too (6 rescans, no device changed), and init.yml's VMFS-only refresh on esx08/09 rescanned HBAs again
  right after the real rescan. Fixed: rescan_hba: false on those three tasks. New tests: every scanhba task states
  rescan_hba; start.yml never rescans HBAs. HBA rescans left where a device appears or grows: init (new volume),
  cleanup/retire (device gone), execute (LUN grew). Lint and offline clean; the next start is its lab check.
- Owner rule 2026-11-21: an HBA rescan only when a LUN is connected, resized or disconnected; a VMFS rescan only when a
  LUN is attached. Written into skills vmfs-datastore-expand ("When to rescan" table), dsgrow-demo (Rules) and
  expansion-safety-review (WARN row). Code brought in line: cleanup.yml and retire_demo01.yml (disconnect) rescan_vmfs
  true -> false; roles/dsgrow_vmfs (resize) other-hosts refresh rescan_vmfs true -> false (refresh_storage only; the
  verify step still requires the grown capacity on every host: the next execute is its lab check). New test
  test_rescans_only_where_a_lun_changes (allow-list per file + task). Unit 122 passed, rulebook test and offline clean,
  lint clean on the changed files (the full lint has 30 findings in tests/probes/w01_*, ops_* probes not part of this).
- Reset (owner: "reset the demo"): cleanup `.state/logs/cleanup-20261121T071419Z.log` ok=63 changed=11 failed=0, read back
  clean (datastore + volume absent, lock released); then init `.state/logs/init-20261121T071709Z.log` ok=68 changed=13
  failed=0, about 2 min 40 s, first runs with the rescan rule (cleanup: HBA rescan only; init: VMFS rescan on esx08/09
  without a second HBA rescan, both mounted). Read-back: dsgrow-demo01 199.75 GiB at 1.8 %, volume 200 GiB, lock released.
- Start (owner: "start the demo", `.state/logs/start-20261121T083622Z.log`): 85.0 % after 44 s, no HBA rescan. Owner saw
  "Refresh host storage system" at 8:37 (start's RefreshStorageSystem on esx07-09, before measuring and after the
  disk). Followed: proposal 08:38:02 (2a5cd7c6-...) 256/272/288 GiB; click 08:41:31 "button dsgrow_approve_3 'Grow
  to 288 GiB (→ 59%)'"; done 08:42:28 (55 s): volume 200 -> 288 GiB, VMFS 199.75 -> 287.75 GiB. First execute without
  the VMFS rescan: esx08/09 got refresh_storage only and verify (capacity on every host) passed. Read-back 59.0 %.
- Owner chose option 1 for the host storage refreshes in start: new module plugins/modules/dsgrow_datastore_refresh.py
  (Datastore.RefreshDatastoreStorageInfo, datastore-scoped, synchronous, no host task; check mode skips the call).
  start.yml: no vmware_host_scanhba at all; refresh before measuring, refresh in an until-loop (>= 80 %) after the
  disk, then the backing read. Skills vmfs-datastore-expand, dsgrow-demo, vcf-ops-webhook updated;
  test_start_never_rescans_hbas now asserts no scanhba and two dsgrow_datastore_refresh tasks. Unit, ansible-doc,
  syntax, lint, offline clean. Not yet run on the lab: the next start is its check.

- Owner: "the goal for this automation is to alert the admin every time the datastore passes the critical limit and
  propose them to expand". The propose throttle was per resource_id (4 h): a second crossing on the same datastore
  within 4 h (e.g. growing the demo disk back to 85 % after the 08:38 proposal) would have been dropped. Now per
  resource_id + alert_id, with `event.payload.alert_id is defined` in the condition. New fixtures
  new_datastore_again.json (same datastore, new alert after the cancel: proposes) and no_alert_id.json (ignored);
  the propose stub records alert_id; test_rulebook expects 5 calls. Rulebook test, unit 153, lint clean. Docs: CLAUDE.md,
  plan.md, dsgrow.yml comment, payload-templates README, skills eda-rulebook and vcf-ops-webhook. Safety review: PASS
  with a warning (more proposals for a flapping alert; overlap still refused by the lock). Not live until the
  rulebook container restarts (gated).
- Owner: "yes, restart the rulebook". `docker compose restart rulebook` at 08:52 (lock released, nothing in flight).
  Read-back: container up, "Waiting for events, ruleset: dsgrow datastore capacity", 0.0.0.0:5080 and 127.0.0.1:5081
  listening, the loaded rulebook groups by resource_id + alert_id, no errors in the log. The restart also cleared the
  in-memory 08:38 throttle. Rollback: the previous group_by line + restart.
- Owner grew the demo disk to 85 % by hand: proposal + execute worked (384 GiB at 08:57:47). Owner: "two refresh host
  storage systems, one at 8:58:05 and another 3 seconds later" = execute's unconditional refresh of esx08/esx09 after
  the expand (and the HBA rescan step also refreshed all three hosts on top of the rescan). roles/dsgrow_vmfs now: HBA
  rescan with refresh_storage false; read the backing; refresh only the hosts whose device is still below target
  (dsgrow_lagging), re-read; after the expand read the backing and refresh only the hosts whose VMFS capacity is below
  target x (1 - tolerance), re-read; verify unchanged. Test test_execute_refreshes_only_lagging_hosts; the two
  host-list expressions checked against sample data in the eda image; lint, unit 123, offline clean; skill
  vmfs-datastore-expand updated. Safety review: PASS (no write changed, verify unchanged, a lagging host still
  fails verify = safe state). Live for the next execute (the rulebook container mounts the repo).
- Reset (owner: "reset the demo"): cleanup `.state/logs/cleanup-20261121T090358Z.log` ok=63 changed=11 failed=0, read back
  clean; init `.state/logs/init-20261121T090623Z.log` ok=68 changed=13 failed=0, about 2 min 35 s. Read-back:
  dsgrow-demo01 199.75 GiB at 1.8 %, volume 200 GiB, lock released.
- Owner: "init and start are fine. we need only cleanup to bring the env to the clean initial state. the reset option
  is not needed". Removed the reset intent (cleanup then init) from CLAUDE.md rule 10 and skill dsgrow-demo; "reset
  the demo" is no longer a go-ahead: ask whether cleanup is meant.
- Owner: template name dsgrow-ubuntu-2604-tpl -> ubuntu-srv-2604-tpl. Repo: inventory demo.yml (dsgrow_demo.template, the
  only value code reads: init clones from dd.template, setup/template.yml deploys/reads it), CLAUDE.md, skill
  dsgrow-demo, setup/README.md step 12. No literal elsewhere (tests use dd.template; nested-vcf has no reference).
  vCenter: the owner renames the template by hand (rule 8: no rename task in this repo; a redeploy would leave a
  second copy). Read-back after the rename: `bash tools/setup.sh setup/template.yml` (forced --check) must find
  it under the new name in /vcf9-1-w02-dc/vm/dsgrow on vcf9-1-w02-ds01 and plan nothing.
- Owner renamed the template in vCenter. Read-back `bash tools/setup.sh setup/template.yml` (check,
  `.state/logs/template-20261121T091646Z-check.log`): ubuntu-srv-2604-tpl found, the identity assert passed (a template,
  folder /vcf9-1-w02-dc/vm/dsgrow, datastores [vcf9-1-w02-ds01]), deploy and mark skipped, ok=11 changed=0 failed=0.
- Manual demo (owner added a disk by hand): proposal 09:21:01 (3893f083-...) at 89.0 % (256 / 288 GiB, 304 refused);
  click 256 GiB 09:21:12. Execute FAILED verify at 09:22:12: volume 200 -> 256 GiB, VMFS still 199.75 GiB on all hosts
  (safe partial state). Cause, my 09:0x change: the HBA rescan without refresh_storage showed the grown device on
  every host, but vmware_host_datastore auto_expand on esx07 returned ok (no space found; it was changed at 08:42 and
  08:58). The conditional post-expand refresh then refreshed all three (none had grown). Owner: "retry the task as
  is to confirm ... then go back". Owner clicked 256 GiB again (09:27:20, same request, code unchanged): expand
  changed at 09:28:00 (the 09:22 refreshes had updated the hosts), the post-expand refresh was skipped (no host
  lagged), done 09:28:02. Read-back 69.5 % of 255.75 GiB, volume 256 GiB, lock released.
- Fix: the HBA rescan keeps refresh_storage: true (required for the expand); the lagging-device refresh removed;
  the post-expand refresh stays conditional (the 09:28 retry proves the hosts see the grown VMFS without it, which
  removes the two 08:58-style refreshes the owner reported). Test and skill updated; unit 123, lint, offline clean.
- Cleanup (owner: "now clean up the environment", `.state/logs/cleanup-20261121T093115Z.log`): removed dsgrow-demo-vm
  (with the hand-added disk), dsgrow-demo01 datastore and the 256 GiB volume; ok=63 changed=11 failed=0, HA policy
  restored. Read-back: datastore and volume absent, lock released.

## 2026-11-21: GitHub (public)

- Owner: store the repo on GitHub with a README, a compact CLAUDE.md, skills of reasonable size; public, history kept
  without personal data. Lab addresses moved to the gitignored inventory/vcf9-1/group_vars/dsgrow/site.yml
  (dsgrow_site; template inventory/site.example.yml), loaded by the inventory and every vars_files list; values
  checked to render with the right types through the inventory and two read-only probes (fa_identity, demo_status).
  test_no_private_addresses_committed guards it. The owner's name and Slack ID replaced in this file. Local only
  (.gitignore): site.yml, .sync.env, tests/probes/w01_* (nested-vcf w01 investigation). CLAUDE.md 230 -> 82 lines;
  skills rewritten to 40-74 lines each; new skill lab-change-gate; README.md written; .gitattributes keeps LF.
- Owner: "many files use specific references to the local environment (vcf9-1, w01...). Those should be made
  general, so that the demo can run in a similar env; the prerequisites should state what the lab env has to be like".
  Environment from .env (DSGROW_ENV, DSGROW_LAB_DIR, DSGROW_LAB_VAULT_PASS; template .env.example; tools/env.sh passes
  -e dsgrow_env, compose uses it for the rulebook inventory and ANSIBLE_INVENTORY; ansible.cfg names none). Every
  vars_files path is inventory/{{ dsgrow_env }}/. New per-env lab.yml: admin secrets files, SSH user/keys, optional
  jump host, OVA dir, and the mapping of the lab vault's names to dsgrow_lab_vc_admin_password /
  dsgrow_lab_ops_admin_password / dsgrow_lab_fa_admin_tokens (vcf9-1: nested-vcf mounted at /lab). dd_admin and the
  vCenter setup take dsgrow_vcenters[].admin_user (default administrator@<sso domain>). demo/retire_demo01.yml removed
  (one-time, done 2026-11-20; in git history). Lab-only investigation probes moved to inventory/vcf9-1/probes/;
  general probes read the inventory (dsgrow_vcfops_url, dsgrow_demo). Fixtures and test data use example.lab names.
  New inventory/example/ (every file commented) and README prerequisites (core + demo). Tests:
  test_code_is_environment_neutral. Validated read-only on the lab: probes demo_status, fa_identity, ops_dsgrow_objects,
  ops_read, inventory/vcf9-1/probes/eui_serial (fixed a stale name->key lookup from the 2026-11-20 rename) and esx_nqn;
  dry runs of demo/init (SSH NQN read through the jump host, attach decision unchanged), setup/template, vcfops,
  fa_user, vcfops_interval, vcenter_account; rendered guest SSH args; offline, rulebook test, lint clean. Run host:
  .env created (DSGROW_ENV=vcf9-1); the running rulebook container is unchanged (same inventory).
- Owner: the tests/probes/w01_* files (19, gitignored, the nested-vcf w01 repair investigation) moved to the nested-vcf
  checkout, tests/probes/w01/ (with a README), on the workstation and the run host, each machine's own copy; their
  .gitignore entry and test exclusions removed.
- Owner: "arrange the structure so that all the locally related files are in a separate directory, not mixed with the
  generic project ones" (choice: in git, own directory). environments/<env>/{inventory,probes,docs}: vcf9-1 (the
  reference lab: its inventory incl. the gitignored site.yml, its probes, this history and the plan) and example/
  (the template); environments/README.md and environments/vcf9-1/README.md. Every path updated (vars_files, compose,
  tools/env.sh, probe.sh, tests, docs, skills); test_lab_files_live_under_environments. Validated on the run host:
  unit 157, offline, rulebook test, lint 0, probes demo_status/fa_identity/ops_read/vcf9-1 eui_serial and dry runs
  of demo/init, setup/vcfops, setup/fa_user all failed=0. The running rulebook still uses the old inventory path until
  it is recreated (gated); the old inventory/ and docs/ stay on the run host until then.
- Owner: "yes, recreate the rulebook container". `docker compose up -d rulebook` at 11:49 (both locks released, nothing in
  flight). Read-back: up, command `--inventory environments/vcf9-1/inventory`, 0.0.0.0:5080 and 127.0.0.1:5081
  listening, "Waiting for events", no errors; the in-memory throttle was cleared. Then the stale copies on the run
  host (docs/, inventory/; site.yml identical to the new one) removed; demo_status probe failed=0; bridge unchanged.
- Full run on the environments/ layout. Init (owner: "initialize the demo", `.state/logs/init-20261121T120250Z.log`):
  ok=68 changed=13 failed=0, about 2 min 35 s; NQNs read on esx07-09 through the jump host (lab.yml ssh_jump), the
  demo VM reached over SSH with the new dd_ssh_proxy args, cloned from ubuntu-srv-2604-tpl. Read-back:
  dsgrow-demo01 199.75 GiB at 1.8 %, volume 200 GiB, lock released.
- Start (owner: "start the demo", `.state/logs/start-20261121T120741Z.log`): 85.0 % after 38 s, 161.94 GiB disk; first lab run of
  dsgrow_datastore_refresh (before measuring and after the disk, both ok first try, no host task). Proposal 12:09:06
  (7a7369a8-...): 256 / 272 / 288 GiB. Click 12:09:51 "button dsgrow_approve_2 'Grow to 272 GiB (→ 62.4%)'".
  Execute: HBA rescan + refresh on esx07-09, expand on esx07 changed, the conditional post-expand refresh skipped (no
  host lagged), verify ok. Read-back: volume 272 GiB, dsgrow-demo01 62.5 % of 271.75 GiB, lock released.
- Cleanup (owner: "now clean up the environment", `.state/logs/cleanup-20261121T121215Z.log`): removed dsgrow-demo-vm, the
  datastore and the 272 GiB volume; ok=63 changed=11 failed=0. Read-back: datastore and volume absent, lock released,
  host group vcf9-1-w02 still exactly esx07-09. The full run on the environments/ layout (init, start, approve,
  execute, cleanup) passed end to end.
- Host NQN from the vSphere API (owner: "weird there isn't an esxi api call that returns that info"). pyVmomi's type
  info: HostSystem.hardware.systemInfo.qualifiedName (HostQualifiedName[], type nvmeQualifiedName, vSphere 7.0 U3+).
  Read through vCenter for esx07-09: exactly the NQNs esxcli gave. The 2026-11-20 probe had searched only
  config.storageDevice (adapters, nvmeTopology: the array's subnqn). demo/tasks/attach_target.yml now reads it with
  vmware_host_facts (schema vsphere, properties [hardware.systemInfo]); SSH to ESXi, esx_ssh_user/esx_ssh_key and
  dd_esx_key/dd_esx_user removed; README demo prerequisites (no ESXi SSH, ESXi 7.0 U3+), dsgrow-demo and
  vmfs-datastore-expand skills, esx_nqn probe updated. Checked: unit 157, lint, offline, init dry run (same host
  group decision, NQNs from vCenter), esx_nqn probe failed=0. First real use: the next init.
- Init (owner: "initialize the demo", `.state/logs/init-20261121T130318Z.log`): ok=67 changed=13 failed=0, about 2 min 25 s.
  First real run of the NQN read from vCenter (no SSH to ESXi): host group vcf9-1-w02 (exactly esx07-09) chosen as
  before. Read-back: dsgrow-demo01 199.75 GiB at 1.8 %, volume 200 GiB, lock released.
