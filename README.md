# vcf-datastore-autogrow

Human-approved growth of VMFS datastores on Everpure FlashArray, triggered by VCF Operations.

When a VMFS datastore runs out of space, it proposes how much to grow it, asks an admin in Slack, and does the
whole expansion once the admin picks a size: it grows the Everpure FlashArray volume behind the datastore, rescans
the ESXi hosts, expands the VMFS and reports back in the same Slack thread.

- **Trigger:** a VCF Operations alert (datastore used ≥ 80 %, or capacity time remaining below a threshold).
- **Decision:** a human, in Slack. Nothing grows without a click from an allow-listed approver.
- **Action:** ansible-rulebook + Ansible playbooks. Grow only; a request that breaks a cap is refused, never clamped.

It does one thing: grow a datastore. Shrinking, creating, deleting and migrating datastores are out of scope.
"Auto" means the work after the decision: nothing grows without an approver's click.

**Status: a demo.** It was built and run on one lab (VCF 9.1, FlashArrays over NVMe/TCP), not in production. It is
careful about *what* it changes (the safety rules and the tests behind them), but it is not hardened as a network
service: the `vcfops` webhook (port 5080) is plain HTTP behind a bearer token, **nothing in this repository sets up a
firewall** (a demo does not need one), and the FlashArray connection is not certificate-checked. Volumes presented over
SCSI are handled by the code but have never been run on a lab. The details are in [Security notes](#security-notes):
read them before you run this anywhere that matters.

Inside the code the project is called `dsgrow`: the variable and role prefix, the event schemas
(`dsgrow.vcfops.v1`, `dsgrow.approval.v1`), the containers and the names of the objects it creates.

## How it works

```
VCF Operations alert "dsgrow - datastore capacity"
  |  Webhook Notification plugin, payload schema dsgrow.vcfops.v1 (new / updated / canceled), bearer token
  v
ansible-rulebook (container dsgrow-eda)
  |  source vcfops     0.0.0.0:5080, token
  |  source approvals  127.0.0.1:5081, token + HMAC signature
  |
  |-- propose  (vcfops, once per alert) -> playbooks/propose.yml   read-only: datastore -> device -> FlashArray
  |                                                                volume, preflight, size options, Slack message
  |-- cleared  (vcfops, canceled)       -> playbooks/cleared.yml   updates the Slack message
  |-- approved (approvals only)         -> playbooks/execute.yml   the only writer
  v
Slack  -- admin clicks a size -->  Slack bridge (Bolt, Socket Mode, container dsgrow-bridge)
                                   approver allow-list, bounds check, signed approval event to 127.0.0.1:5081

execute.yml: request file + expiry + approver + lock -> full preflight -> FlashArray volume to the absolute target
             size -> HBA rescan + storage refresh -> expand VMFS on one host -> verify on every host -> Slack thread
```

The rulebook is the hub, so every step is visible in one log. The approval path trusts nothing it receives:
`execute.yml` re-reads the request file that `propose.yml` wrote, re-checks the approver and re-runs every check
against live data. A forged or replayed event can't grow anything.

The rulebook also starts no playbook for an event with an unexpected field: ansible renders event strings as templates
when a playbook reads them, so every field of an alert or an approval must match an anchored allow-list in the rulebook
first (`rulebooks/datastore_capacity.yml`). The `vcfops` webhook is plain HTTP behind a bearer token (see Security notes).

## Safety model

- **Grow only, absolute sizes.** The target is absolute bytes; a double click or a retried job is a no-op.
- **One datastore, one extent, one volume.** Multi-extent, non-VMFS 6, non-Everpure or ambiguous devices are refused.
- **Caps** per request (percent and absolute), a maximum volume size (64 TiB, the VMFS 6 limit) and minimum array
  free space after growth.
- **One request per datastore in flight**, requests expire (4 h), every change names its approver.
- **Partial failure is safe.** If the volume grew but VMFS didn't, the space is unused; approving again finishes it.
- **What the tests enforce** (and only that): `tests/unit/test_repo_safety.py` scans the setup, role and playbook text for
  a few forbidden spellings and checks the execute order, the rescan rule and the demo's own removals;
  `tests/unit/test_rule8_inventory.py` parses every task file and holds the execute path, the setup steps and the probes
  to a reviewed list of modules, allows no removal outside `demo/` except the one named rollback
  (`setup/vcfops_cleanup.yml`, VCF Operations objects this repo created) and keeps probes to GET plus authentication.
  `tests/offline/test_flow.py` runs the real propose, execute, deny and cleared playbooks against a fake FlashArray,
  vCenter and Slack: every refusal of rules 1-6, a replayed approval, a denied request, a Slack error after the work.
  `tests/rulebook/` starts the real rulebook with hostile events. `tools/offline.sh` runs all of it (no network).

## Security notes

What a deployment still has to take care of, and what the code does about the rest:

- **Event strings are templates.** Ansible renders the strings of an event when a playbook reads them, so every field a
  playbook reads must pass an allow-list in the rulebook before the playbook starts (`rulebooks/datastore_capacity.yml`):
  strict character sets for the identifiers that pick the datastore (printable ASCII only, so not even a trailing
  newline gets through), and for the text shown in Slack only "no Jinja opener". Fields nothing reads have no pattern, so an unexpected value there never drops an alert (they stay inert; a
  test runs the real playbooks to prove it). A rejected alert is logged as `dsgrow REJECTED` with the values it saw. The
  bridge cleans the approver's name the same way.
- **The `vcfops` webhook (port 5080) is plain HTTP behind a bearer token, and nothing here sets up a firewall.** This is a
  demo, and a demo does not need one; it is stated here so that nobody assumes otherwise. Whoever can reach the port
  and has the token can post a forged alert (a proposal that an approver must still click: nothing grows without the
  click) or cancel a pending one; the `approvals` source, which starts the expansion, is loopback only, with its own
  token and an HMAC. Beyond a demo: allow port 5080 only from the VCF Operations nodes, set `DSGROW_EDA_VCFOPS_BIND` in
  the run host's own `.env` (not a synced file) to the address on their network, and prefer a TLS proxy on the run host
  (bind the source to `127.0.0.1`, set `dsgrow_eda_vcfops_scheme: https` in the inventory, import the proxy's
  certificate into VCF Operations).
- **FlashArray TLS is not verified.** The `everpure.flasharray` 1.46.0 modules (every runtime FlashArray call) have no
  option to verify the array's certificate, whatever `validate_certs` says in `flasharrays.yml` (that key and
  `api_version` are read only by the REST probes). A host on the path to the management address could read the API
  token: use a management network you trust, until the collection offers an option.
- **Certificate checks of the setup steps, the demo and the probes** follow the inventory: `validate_certs` of each
  vCenter and array, `dsgrow_vcfops_validate_certs` for VCF Operations. They are `true` unless the inventory says
  otherwise; a lab with self-signed certificates says `false` in its own files.
- **An alert names its datastore by name only.** A proposal is refused for a datastore less full than
  `dsgrow_alert_min_used_pct` (75 %), which catches the same name in a vCenter that is out of scope; also limit the VCF
  Operations notification rule to the datastores of the configured vCenters.
- **One request, one approved size; a denial is recorded.** A request that is executing or done accepts only the size it
  was approved for, and a replay of that approval does nothing; a denial is an event that marks the request `denied`.
- **The vCenter account.** The `dsgrow` role has one privilege, `Host.Config.Storage`, which also allows creating,
  removing and unmounting datastores: vSphere has nothing narrower, so "grow only" rests on the playbooks and their
  tests. vCenter's default SSO policy expires a password after 90 days (check yours): `setup/vcenter_account.yml` does
  not touch an existing user, so rotate by hand, then update `.state/secrets/vcenter-<key>.yml`, run `setup/deploy.yml`
  and `docker compose up -d rulebook bridge`.
- **The human gate is only as strong as the approvers' Slack accounts.** Nothing grows without a click from an allow-listed
  approver, so an approver's account is the last line: require single sign-on and two-factor authentication for the people
  in `DSGROW_APPROVER_IDS`, and keep that list short. The bridge checks the approver's Slack user ID, not the channel.
- **Deployment.** Both services run with a read-only root filesystem (`/tmp` and, for the rulebook, `state/` are all they
  can write) and no capability; the rulebook container also sees the code read-only and none of `.state/`
  (`compose.yaml`). `tools/sync.sh` refuses to copy over a datastore that is proposed or executing (`SYNC_FORCE=1`
  overrides, after the owner's go-ahead); the running rulebook still executes the files in that directory, so sync only
  between requests. `tools/probe.sh` and `tools/setup.sh` take a path of one shape and a short list of options.

## Sizing

For each target utilisation `u` in `dsgrow_target_utilization_options` (default 70 / 65 / 60 %):
`target = roundup(used / u, dsgrow_size_step_bytes)`. An option is offered only if it is bigger than the volume and
every cap holds. "Custom…" takes any size; it is checked the same way.

| Policy (`playbooks/group_vars/all/dsgrow.yml`) | Default |
|---|---|
| `dsgrow_size_step_bytes` | 256 GiB (the lab inventory uses 16 GiB) |
| `dsgrow_max_growth_pct` / `dsgrow_max_growth_bytes` | 50 % / 10 TiB per request |
| `dsgrow_max_volume_bytes` | 64 TiB |
| `dsgrow_fa_min_free_pct_after` | 20 % array free space after growth |
| `dsgrow_request_ttl_hours` / rulebook throttle | 4 h / 4 h per alert |
| `dsgrow_capacity_tolerance_pct` | 1 % (verify: VMFS capacity vs target) |

## Components

| Component | Version |
|---|---|
| VCF Operations | 9.1.1 (Webhook Notification plugin; symptoms, alert, template and rule created by `setup/vcfops.yml`) |
| ansible-rulebook, `ansible.eda` | 1.3.2, 2.13.0 |
| ansible-core | 2.21.4 |
| `community.vmware` | 6.3.0 (plus two small pyVmomi modules in `plugins/modules/`) |
| `everpure.flasharray` | 1.46.0, with `docker/patches/` applied for issue #1103 (fixed upstream, not yet released) |
| Slack bridge | Python 3.12, `slack_bolt` 1.27.0, Socket Mode (no inbound exposure) |

Devices: SCSI `naa.624a9370` + serial, or NVMe `eui.00` + serial[0:14] + `24a937` + serial[14:24].

## Repository layout

```
rulebooks/datastore_capacity.yml   the rulebook
playbooks/                         propose.yml, execute.yml, cleared.yml, policy defaults in group_vars/all/
roles/                             dsgrow_resolve, dsgrow_preflight, dsgrow_flasharray, dsgrow_vmfs, dsgrow_state, dsgrow_slack
plugins/filter/dsgrow.py           size math, device <-> serial, caps (pure functions)
plugins/modules/                   dsgrow_datastore_backing (read-only), dsgrow_datastore_refresh
bridge/                            the Slack bridge and its tests
slack/manifest.yml                 the Slack app
vcf-ops/payload-templates/         the three VCF Ops webhook templates
setup/                             one-time setup, one gated step at a time (setup/README.md)
demo/                              a self-contained demo: init / start / cleanup
docker/, compose.yaml              the dsgrow-eda and dsgrow-bridge images and services
tests/                             unit, bridge, rulebook (fixtures + stub playbooks), offline logic, read-only probes
tools/                             sync, setup and probe runners (.env), the demo follower
.claude/skills/                    working notes for Claude Code (one skill per area)

environments/                      everything specific to one lab, nothing else (environments/README.md)
  example/                         the template: inventory/ to copy for a new lab
  vcf9-1/                          the reference lab: inventory/, probes/, docs/ (plan, history of every change)
```

## Prerequisites

What the environment must provide. The reference lab (environment `vcf9-1`, VCF 9.1.1) has all of it.

**For the expansion**

- **VCF Operations 9.1** (tested 9.1.1) with a vCenter adapter instance per vCenter and an admin account for its
  suite-api: `setup/vcfops.yml` creates the symptoms, the alert definition, the Webhook Notification plugin
  instance, its payload template and the notification rule.
- **vCenter** (tested VCF 9.1) with the datastores to grow: VMFS 6, each on exactly one extent, backed by one
  FlashArray volume. An SSO administrator account for `setup/vcenter_account.yml`, which creates the least-privilege
  `dsgrow` user and role (with `govc`).
- **Everpure FlashArray** with REST 2.x (tested Purity 6.12, REST 2.59), volumes presented over NVMe-oF (`eui.`
  devices) or SCSI (`naa.624a9370…`), not in pods. An admin API token for `setup/fa_user.yml`, which creates the
  `dsgrow` `storage_admin` user.
- **A Linux run host** with Docker and Compose, about 2 GB of disk for the images. VCF Operations must reach it on
  TCP 5080 (the webhook, plain HTTP); it must reach vCenter, the arrays and VCF Operations on HTTPS, and Slack outbound (Socket
  Mode needs no inbound access).
- **A Slack workspace** where you can install an app (`slack/manifest.yml`), a channel for the proposals, and the
  member IDs of the approvers.

**For the demo** (`demo/`), in addition

- A vSphere cluster (ESXi 7.0 U3 or later) whose hosts are **NVMe-oF initiators** of the array (tested NVMe/TCP).
  The array has one host object per ESXi host carrying its host NQN; if those host objects are in a host group, the
  group must contain exactly these hosts. The demo reads each host's NQN from vCenter
  (`hardware.systemInfo.qualifiedName`), so no SSH to the hosts is needed. SCSI-attached hosts aren't handled by the
  demo's connect step.
- An existing datastore for the VM template (never the demo datastore), a port group with a free static IPv4
  address, its gateway and DNS. The run host must reach that address over SSH, directly or through a jump host
  (`lab.yml`), to finish the VM's setup.
- The **Ubuntu 26.04 cloud image OVA** (with cloud-init) and its SHA-256, in the directory `lab.yml` names.
- About 200 GiB of thin provisioned space on the array (the demo writes almost nothing). vSphere HA may be on:
  cleanup moves its heartbeat datastores off the demo datastore and puts them back.
- Optional: a one-minute VCF Operations collection interval for the demo vCenter (`setup/vcfops_interval.yml`), so
  the alert comes within about a minute instead of five.

## Getting started

1. Clone the repo on the run host (or edit on a workstation and copy it with `tools/sync.sh`).
2. `cp .env.example .env` and set `DSGROW_ENV` to your environment's name; set `DSGROW_LAB_DIR` if the lab's admin
   vault, SSH keys or OVA live outside the repo (mounted read-only at `/lab`).
3. `cp -r environments/example environments/<env>` and edit `inventory/group_vars/dsgrow/`: `vcenters.yml`, `flasharrays.yml`,
   `eda.yml`, `policy.yml`, `lab.yml` (admin credentials, SSH keys, OVA) and, for the demo, `demo.yml`. Copy
   `environments/example/inventory/group_vars/dsgrow/site.yml.example` to `environments/<env>/inventory/group_vars/dsgrow/site.yml` (gitignored) and put your
   addresses there.
4. Follow `setup/README.md` in order. Every step has a dry run, a rollback and a read-back.

## Demo

`demo/` builds a disposable environment on the lab and removes it again:

- `demo/init.yml`: a 200 GiB volume and VMFS datastore, connected to the hosts' host group, and an Ubuntu VM.
  It finds the hosts' FlashArray host objects by their NVMe NQNs.
- `demo/start.yml`: hot-adds one thick lazy-zeroed disk that takes the datastore to 85 % in seconds.
- Then the product runs: the alert arrives within about a minute, the proposal reaches Slack, an approver clicks,
  and the expansion completes in under a minute.
- `demo/cleanup.yml`: removes everything init created, each object checked against the recorded identity.

The demo's objects and sizes are in `environments/<env>/inventory/group_vars/dsgrow/demo.yml`. It can be run in two
ways.

**Fully assisted.** Every step is a playbook, run on the run host:

```bash
bash tools/setup.sh demo/init.yml --confirm init.yml
bash tools/setup.sh demo/start.yml --confirm start.yml
python3 tools/demo_follow.py          # follows the proposal, the click and the expansion (read-only)
bash tools/setup.sh demo/cleanup.yml --confirm cleanup.yml
```

**Start by hand in the vSphere Client.** Run init and cleanup as above, but fill the datastore yourself, which shows
the audience that an ordinary vSphere operation triggers the flow:

1. Edit the settings of the demo VM (`dsgrow-demo-vm`) and add a hard disk on the demo datastore (`dsgrow-demo01`),
   **thick provision lazy zeroed** (a thin disk takes no space; eager zeroed takes minutes). About 166 GiB takes a
   fresh 200 GiB demo datastore to 85 %; anything above about 157 GiB crosses the 80 % alert threshold.
2. Right-click the datastore and choose *Refresh Capacity Information*. Without it vCenter's cached used space can
   lag, and the alert comes minutes later.
3. Follow it with `python3 tools/demo_follow.py --since <UTC time of the init>`, or just watch Slack.

Keep the disk on the demo VM and on the demo datastore: cleanup refuses to remove a datastore that holds other VMs,
or a VM with disks elsewhere. Cleanup removes the added disk together with the VM.

## Tests

```bash
python -m pytest tests/unit bridge/tests          # pure Python, anywhere
bash tools/offline.sh                             # syntax + offline playbook logic (run host, no network)
docker run --rm --network none -v "$PWD:/work" -w /work dsgrow-eda:1 \
  python -m pytest -q -p no:cacheprovider tests/rulebook       # the rulebook against the fixtures
docker compose run --rm -T eda ansible-lint
```

## Working on it with Claude Code

`CLAUDE.md` holds the rules every session follows; `.claude/skills/` holds one short skill per area (rulebook, VCF
Ops, FlashArray, VMFS, Slack, safety review, lab changes, demo). Each environment's `docs/history.md` records every change made to that lab, with its
evidence.

## Prior art

Everpure's [flasharray-eda](https://github.com/sdodsley/flasharray-eda) prototype has a capacity rulebook with an
approval gate and a guarded volume expansion. It is array-driven and doesn't touch VMFS; dsgrow starts from the
VCF Operations alert and finishes the vSphere side.

## License

Apache License 2.0, see [LICENSE](LICENSE).
