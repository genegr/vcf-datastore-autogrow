# dsgrow: human-approved VMFS datastore expansion

When a VMFS datastore runs out of space, dsgrow proposes how much to grow it, asks an admin in Slack, and does the
whole expansion once the admin picks a size: it grows the Everpure FlashArray volume behind the datastore, rescans
the ESXi hosts, expands the VMFS and reports back in the same Slack thread.

- **Trigger:** a VCF Operations alert (datastore used ≥ 80 %, or capacity time remaining below a threshold).
- **Decision:** a human, in Slack. Nothing grows without a click from an allow-listed approver.
- **Action:** ansible-rulebook + Ansible playbooks. Grow only; a request that breaks a cap is refused, never clamped.

It does one thing: grow a datastore. Shrinking, creating, deleting and migrating datastores are out of scope.

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
against live data. A forged or replayed event can't do damage.

## Safety model

- **Grow only, absolute sizes.** The target is absolute bytes; a double click or a retried job is a no-op.
- **One datastore, one extent, one volume.** Multi-extent, non-VMFS 6, non-Everpure or ambiguous devices are refused.
- **Caps** per request (percent and absolute), a maximum volume size (64 TiB, the VMFS 6 limit) and minimum array
  free space after growth.
- **One request per datastore in flight**, requests expire (4 h), every change names its approver.
- **Partial failure is safe.** If the volume grew but VMFS didn't, the space is unused; approving again finishes it.
- `tests/unit/test_repo_safety.py` enforces the rules mechanically: no delete/rename/eradicate outside the demo, no
  direct FlashArray REST, only one write module in the execute path, rescans only where a LUN changed, and more.

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
  TCP 5080 (the webhook); it must reach vCenter, the arrays and VCF Operations on HTTPS, and Slack outbound (Socket
  Mode needs no inbound access).
- **A Slack workspace** where you can install an app (`slack/manifest.yml`), a channel for the proposals, and the
  member IDs of the approvers.

**For the demo** (`demo/`), in addition

- A vSphere cluster whose ESXi hosts are **NVMe-oF initiators** of the array (tested NVMe/TCP). The array has one
  host object per ESXi host carrying its host NQN; if those host objects are in a host group, the group must contain
  exactly these hosts. SCSI-attached hosts aren't handled by the demo's connect step.
- **SSH to the ESXi hosts** as root with a key (SSH service on), from the run host directly or through a jump host,
  to read each host's NQN (`esxcli nvme info get`, read-only).
- An existing datastore for the VM template (never the demo datastore), a port group with a free static IPv4
  address, its gateway and DNS, reachable over SSH from the run host (directly or through the jump host).
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

Run with `bash tools/setup.sh demo/<x>.yml --confirm <x>.yml` on the run host; follow an expansion with
`python3 tools/demo_follow.py`. The demo's objects and sizes are in `environments/<env>/inventory/group_vars/dsgrow/demo.yml`.

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
