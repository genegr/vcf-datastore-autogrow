---
name: flasharray-volume-extend
description: Use for anything that touches the Everpure FlashArray in this repo - mapping a VMFS extent (naa or NVMe eui) to its volume, array and volume preflight checks, extending the volume to an absolute target size with the everpure.flasharray modules (patched for issue 1103), and verifying the result.
---

# FlashArray volume mapping and extend

## Modules only (owner 2026-11-20)

Every FlashArray call goes through `everpure.flasharray.*` modules, never REST via `uri`
(`test_no_direct_flasharray_rest`). Never write `purestorage.flasharray.*` (old namespace).

- 1.46.0 has bug #1103: Purity returns `priority_adjustment_value: null`, and `purefa_info` volumes and
  `purefa_volume` on any existing volume fail with "Module failed." Fixed upstream (commit 333dcca), in no release.
  The dsgrow-eda image applies `docker/patches/everpure-flasharray-1.46.0-issue1103.patch`; the build fails if it
  doesn't apply, and `docker/check_flasharray_patch.py` proves it. Bump the collection with the patch; drop the
  patch when a release ships the fix. Run playbooks in `dsgrow-eda:1` (other images aren't patched).
- Arrays: `dsgrow_flasharrays[].name` is the Purity name (shown everywhere); `key` is the stable id that names the
  secret (`dsgrow_secret_fa_token_<key>`), the env var (`token_env`) and the site URL (`dsgrow_site.fa_url[key]`).

## Module behaviour (1.46.0 source)

- `fa_url` is the address without scheme; `api_token` is masked. The token is the `dsgrow` API user's
  (`storage_admin`, created by `setup/fa_user.yml`).
- `purefa_info`: `volumes` keyed by name (`size`, `serial`, `nvme_nguid`, `hosts`, `host_groups`; live only,
  trash in `deleted_volumes`; one connection call per volume); `capacity` (`total_capacity`, `free_space`,
  `total_used`); `hosts` (`volumes` [{volume, hostgroup, nsid}]).
- `purefa_volume name size`: creates, or grows an existing volume only when bigger (a smaller size is silently
  ignored, so preflight refuses it). Digits are bytes. Native check mode. `state: absent` + `eradicate: true`
  destroys and eradicates.
- `purefa_host` / `purefa_hg` with `state: absent`: **without `volume` they delete the host or host group**.
  `test_flasharray_disconnect_always_names_the_volume` enforces `volume`.
- `purefa_user` returns the new token as `user_info` (a string); `api: true` on an existing user rotates it.

## Mapping a datastore to its volume

Serial = 24 hex digits (upper case on the array):
```
SCSI  naa.624a9370 + serial (lower case)
NVMe  eui.00 + serial[0:14] + 24a937 + serial[14:24]      (proven on vcf9-1: inventory/vcf9-1/probes/eui_serial.yml)
```
Filters `dsgrow_device_to_fa_serial` / `dsgrow_fa_serial_to_device` (`plugins/filter/dsgrow.py`) reject anything
else. Then `purefa_info` on every array; exactly one match across all arrays (`roles/dsgrow_flasharray/tasks/find_one.yml`).

## Preflight (`roles/dsgrow_preflight`, refuse with a reason in Slack)

Serial not found or on several arrays; volume destroyed; volume in a pod (out of scope); target ≤ current;
growth over `dsgrow_max_growth_pct` or `dsgrow_max_growth_bytes`; target over `dsgrow_max_volume_bytes`; array free
space after growth below `dsgrow_fa_min_free_pct_after` (growth counted as fully written).

## Extend and verify (`roles/dsgrow_flasharray/tasks/extend.yml`)

Only when current < target: re-read the volume right before the write (same serial, live, same size), then
`purefa_volume name size: <target bytes>` and nothing else. Absolute size, never a delta. The result's size must
equal the target, else the job fails before any vSphere step.

## Never (execute path)

Any module but `purefa_info` and that one `purefa_volume`; `state: absent`, `eradicate`, rename, move, copy, QoS,
protection groups, connections (`test_execute_path_writes_the_array_once_and_only_grows`). Only `demo/` creates,
connects and removes its own volume.
