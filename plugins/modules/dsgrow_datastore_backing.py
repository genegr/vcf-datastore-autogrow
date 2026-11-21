#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read-only: a VMFS datastore's backing as dsgrow needs it (skill vmfs-datastore-expand)."""
from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: dsgrow_datastore_backing
short_description: Read a VMFS datastore's extents and, per mounting host, its mount state and device size
description:
  - Read-only. Finds every datastore with the given name in the vCenter (optionally one datacenter) and reports the
    one it finds with type, VMFS major version, capacity, free space, maintenance mode, extents, and for every host
    that mounts it whether it is mounted and accessible, the VMFS capacity that host sees, and the size of each extent
    device as that host sees it.
  - Never changes anything; supports check mode.
  - Exists because vmware_datastore_info's vSphere schema serialises managed object references in a
    version-specific way, and it does not report per-host device sizes, which the verify step needs after a rescan.
options:
  datastore:
    description: Datastore name.
    type: str
    required: true
  datacenter:
    description: Limit the search to this datacenter.
    type: str
  query_expand:
    description:
      - Also ask the first mounting host (by name order) what an expand would do
        (HostDatastoreSystem.QueryVmfsDatastoreExpandOptions, a query - nothing changes). Returned as C(expand).
    type: bool
    default: false
extends_documentation_fragment:
  - vmware.vmware.base_options
"""

RETURN = r"""
matches:
  description: How many datastores have that name (the caller refuses anything but 1).
  type: int
datastore:
  description: The datastore (empty when matches != 1).
  type: dict
  contains:
    name: {type: str, description: Datastore name.}
    moid: {type: str, description: "The datastore's managed object id in its vCenter (datastore-35)."}
    vcenter_uuid: {type: str, description: "The vCenter's instance UUID; with moid, the datastore's identity (VCF Ops VMEntityVCID)."}
    type: {type: str, description: "Datastore type (VMFS, NFS, vsan, ...)."}
    vmfs_major_version: {type: int, description: VMFS major version (6), 0 when not VMFS.}
    vmfs_uuid: {type: str, description: "The VMFS UUID (esxcli storage filesystem list), empty when not VMFS."}
    vms: {type: list, elements: str, description: Names of the VMs (and templates) registered with files on it.}
    capacity_bytes: {type: int, description: Capacity as vCenter reports it.}
    free_bytes: {type: int, description: Free space as vCenter reports it.}
    used_bytes: {type: int, description: Capacity minus free space.}
    maintenance_mode: {type: str, description: "normal, enteringMaintenance or inMaintenance."}
    accessible: {type: bool, description: Accessible according to vCenter.}
    extents: {type: list, elements: str, description: "Extent device names (naa... / eui...)."}
    hosts:
      type: list
      elements: dict
      description: >-
        One entry per host in the datastore's host list - name, mounted, accessible, mode, vmfs_capacity_bytes (that
        host's view), devices (extent device -> size in bytes as that host sees it, 0 when not seen).
expand:
  description: >-
    Only with query_expand - host (the first mounting host by name), options (how many expand options it offers; 0
    when the device has no new space), fault (the fault the query raised, empty when none; NotFound when the
    datastore is not mounted on that host, measured on the lab 2026-11-19), end_sectors.
  type: dict
  returned: when query_expand
"""

try:
    from pyVmomi import vim
except ImportError:  # handled by PyVmomi
    pass

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.community.vmware.plugins.module_utils.vmware import PyVmomi, vmware_argument_spec


def _host_view(host, ds, extents):
    entry = {"name": host.name, "mounted": False, "accessible": False, "mode": "", "vmfs_capacity_bytes": 0,
             "devices": {e: 0 for e in extents}}
    for mount in ds.host:
        if mount.key == host:
            info = mount.mountInfo
            entry["mounted"] = bool(info.mounted) if info.mounted is not None else False
            entry["accessible"] = bool(info.accessible) if info.accessible is not None else False
            entry["mode"] = info.accessMode or ""
    storage = host.configManager.storageSystem
    if storage is None:
        return entry
    for mi in storage.fileSystemVolumeInfo.mountInfo or []:
        vol = mi.volume
        if isinstance(vol, vim.host.VmfsVolume) and vol.name == ds.name:
            entry["vmfs_capacity_bytes"] = int(vol.capacity or 0)
    for lun in storage.storageDeviceInfo.scsiLun or []:
        if lun.canonicalName in entry["devices"] and isinstance(lun, vim.host.ScsiDisk) and lun.capacity:
            entry["devices"][lun.canonicalName] = int(lun.capacity.block) * int(lun.capacity.blockSize)
    return entry


class DatastoreBacking(PyVmomi):
    def run(self):
        name = self.params["datastore"]
        root = self.content.rootFolder
        if self.params.get("datacenter"):
            dc = self.find_datacenter_by_name(self.params["datacenter"])
            if dc is None:
                self.module.fail_json(msg="datacenter %s not found" % self.params["datacenter"])
            root = dc
        view = self.content.viewManager.CreateContainerView(root, [vim.Datastore], True)
        try:
            found = [d for d in view.view if d.name == name]
        finally:
            view.Destroy()
        result = {"changed": False, "matches": len(found), "datastore": {}}
        if len(found) != 1:
            self.module.exit_json(**result)
        ds = found[0]
        summary = ds.summary
        info = ds.info
        extents, vmfs_major, vmfs_uuid = [], 0, ""
        if isinstance(info, vim.host.VmfsDatastoreInfo) and info.vmfs is not None:
            extents = [e.diskName for e in (info.vmfs.extent or [])]
            vmfs_major = int(info.vmfs.majorVersion or 0)
            vmfs_uuid = info.vmfs.uuid or ""
        capacity, free = int(summary.capacity or 0), int(summary.freeSpace or 0)
        result["datastore"] = {
            "name": ds.name,
            "moid": ds._moId,
            "vcenter_uuid": self.content.about.instanceUuid or "",
            "type": summary.type,
            "vmfs_major_version": vmfs_major,
            "vmfs_uuid": vmfs_uuid,
            "vms": sorted(vm.name for vm in (ds.vm or [])),
            "capacity_bytes": capacity,
            "free_bytes": free,
            "used_bytes": capacity - free,
            "maintenance_mode": summary.maintenanceMode or "normal",
            "accessible": bool(summary.accessible),
            "extents": extents,
            "hosts": sorted((_host_view(m.key, ds, extents) for m in ds.host), key=lambda h: h["name"]),
        }
        if self.params.get("query_expand") and ds.host:
            host = sorted((m.key for m in ds.host), key=lambda h: h.name)[0]
            result["expand"] = _expand_options(host, ds)
        self.module.exit_json(**result)


def _expand_options(host, ds):
    """Read-only: what QueryVmfsDatastoreExpandOptions answers on `host` (options found, or the fault)."""
    out = {"host": host.name, "options": 0, "fault": "", "end_sectors": []}
    try:
        options = host.configManager.datastoreSystem.QueryVmfsDatastoreExpandOptions(datastore=ds) or []
        out["options"] = len(options)
        for o in options:
            try:
                out["end_sectors"].append(int(o.spec.partition.partition[-1].endSector))
            except (AttributeError, IndexError, TypeError):
                pass
    except Exception as e:  # report the fault, never fail a read
        out["fault"] = "%s: %s" % (type(e).__name__, getattr(e, "msg", None) or str(e))
    return out


def main():
    spec = vmware_argument_spec()
    spec.update(datastore=dict(type="str", required=True), datacenter=dict(type="str"),
                query_expand=dict(type="bool", default=False))
    module = AnsibleModule(argument_spec=spec, supports_check_mode=True)
    DatastoreBacking(module).run()


if __name__ == "__main__":
    main()
