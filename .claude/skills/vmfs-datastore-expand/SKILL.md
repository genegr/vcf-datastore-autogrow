---
name: vmfs-datastore-expand
description: Use for the vCenter side of a datastore expansion - looking up the datastore, its extent and mounting hosts, rescanning after the LUN grew, expanding the VMFS, and verifying capacity on every host. Also the rule for when an HBA or VMFS rescan or a storage refresh is allowed at all (any vmware_host_scanhba task, demo included).
---

# VMFS datastore lookup, rescan and expand

## Modules (community.vmware 6.3.0, read in the source)

- `vmware_host_datastore` `state: present`, `auto_expand: true`: on a host that mounts the datastore it calls
  `QueryVmfsDatastoreExpandOptions` + `ExpandVmfsDatastore(options[0])`. **On a host that doesn't mount it, it tries
  to create a VMFS** on the device, so execute asserts the expand host mounts the datastore right before the task.
- `vmware_host_scanhba` (`rescan_hba` **defaults to true**, `rescan_vmfs`, `refresh_storage`): no check mode.
- Lookup and verify use our read-only module `plugins/modules/dsgrow_datastore_backing.py` (pyVmomi): type, VMFS
  version and UUID, capacity, free, extents, and per host: mounted, accessible, VMFS capacity, device size.
  `query_expand: true` reports the expand options; the query raises NotFound on a host that doesn't mount it.
- `plugins/modules/dsgrow_datastore_refresh.py`: `RefreshDatastoreStorageInfo`, datastore-scoped, no host task.

## Lookup (read-only, `roles/dsgrow_resolve`)

Refuse when the name matches zero or several datastores across `dsgrow_vcenters`, it isn't VMFS 6, has more than one
extent, is in maintenance mode, or is inaccessible on a mounting host. The hosts to rescan are the mounting hosts.
Device IDs: NVMe/TCP on the lab gives `eui.<32 hex>`; SCSI gives `naa.624a9370…` (skill flasharray-volume-extend).

## When to rescan (owner 2026-11-21; enforced by `test_rescans_only_where_a_lun_changes`)

| Event | `rescan_hba` | `rescan_vmfs` | `refresh_storage` |
|---|---|---|---|
| New LUN connected (demo init) | true | true, only on the other hosts after the VMFS is created | true |
| LUN resized (execute, after the FlashArray step) | true | false | true, **required** |
| LUN disconnected (demo cleanup) | true | false | true |
| VMFS grown on one host (execute) | false | false | only on a host that doesn't show the grown VMFS |
| Anything else (VMDK added, used-space reading) | no `vmware_host_scanhba`: use `dsgrow_datastore_refresh` | | |

Every `vmware_host_scanhba` task states `rescan_hba` explicitly. A host storage refresh shows as "Refresh host
storage system" on each host, so run it only where the table says.

Measured 2026-11-21: a rescan without `refresh_storage` showed the grown device on every host, but `auto_expand`
found no space (`ok`) and verify failed at the old size. After a refresh, the retry grew it. After the expand, the
other hosts showed the grown VMFS without a refresh.

## Execute steps (`roles/dsgrow_vmfs`)

1. `rescan_hba: true, refresh_storage: true` on each mounting host, one at a time.
2. Re-read the backing; require the device size ≥ target on every host.
3. Assert the first host mounts the datastore; `vmware_host_datastore` (`datastore_type: vmfs`,
   `vmfs_device_name`, `vmfs_version: 6`, `auto_expand: true`, `state: present`).
4. Re-read; refresh only hosts whose VMFS is below target × (1 − tolerance) (`dsgrow_lagging`); re-read.
5. Verify: capacity ≥ target × (1 − `dsgrow_capacity_tolerance_pct`/100), the same capacity on every host,
   accessible everywhere. Record before/after in the request file and Slack.

Partial failure (volume grown, VMFS not) is safe: the space is unused. Re-sending the approval finishes the job; the
FlashArray step is a no-op at target.

## Service account

`setup/vcenter_account.yml`: SSO user `dsgrow@<sso domain>` (govc `sso.user.create`, no Ansible module), role
`dsgrow` with `Host.Config.Storage` on the datacenter, propagating.

## Never

`state: absent` (unmount), resignature, creating datastores, adding extents, touching any device but the resolved
one. The safety review blocks these.
