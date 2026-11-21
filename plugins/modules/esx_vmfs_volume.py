#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copied unchanged from nested-vcf plugins/modules/ (2026-11-20) for the demo lifecycle (demo/); keep in sync.
# Our own module: no module of the pinned collections mounts or unmounts an existing VMFS volume by its UUID, or detaches
# its device (community.vmware 6.3.0 vmware_host_datastore state=present CREATES a VMFS when the datastore is not
# mounted, and state=absent removes the datastore; vmware.vmware 2.10.0 has nothing for it; searched 2026-11-20). Why it
# exists: the owner's DR demo of 2026-11-10 unmounted vcf9-1-w01-ds01/dr01 on esx04/esx05 and never mounted them again
# (vsphere-dsgrow docs/history.md, 2026-11-19/20); the repair (playbooks/w01_dr_repair.yml) needs unmount, mount and,
# for the demo's leftover vcf9-1-dstest01, detach.

DOCUMENTATION = r"""
module: esx_vmfs_volume
short_description: Mount, unmount or detach an existing VMFS volume on one host, by VMFS UUID, through vCenter
description:
  - Finds the VMFS volume with O(vmfs_uuid) in the host's file system volume info (mounted or not) and refuses anything
    else - a volume the host does not know, a different device than O(device) (the single extent's disk name), a
    volume with more than one extent, or a snapshot/unresolved copy (it never resignatures or force-mounts).
  - O(state=mounted) runs HostStorageSystem.MountVmfsVolume; O(state=unmounted) runs UnmountVmfsVolume (vSphere refuses
    while VMs are registered on it, which the module reports); O(state=detached) unmounts first, then
    HostStorageSystem.DetachScsiLun for the extent's device. O(state=forgotten) is for a device already detached and gone
    from the array: it removes the host's persistent detached-device entry (DeleteScsiLunState). Every call goes through
    vCenter, so it is a recorded task.
  - Rerunnable: once a device is detached the host no longer lists its VMFS volume; O(state=detached) then reports
    unchanged when the device is still known and off, O(state=forgotten) when the host has no entry for it any more.
  - Never formats, creates, removes, resignatures or rescans. Check mode reports what would be done.
options:
  esxi_hostname:
    description: The host's name as vCenter knows it.
    type: str
    required: true
  vmfs_uuid:
    description: The VMFS UUID (as in esxcli storage filesystem list).
    type: str
    required: true
  device:
    description: The extent's device (eui... / naa...) the volume must be on; the module refuses a mismatch.
    type: str
    required: true
  state:
    description: The wanted state on that host.
    type: str
    choices: [mounted, unmounted, detached, forgotten]
    required: true
extends_documentation_fragment: [vmware.vmware.base_options]
"""

RETURN = r"""
before:
  description: The volume on the host before (name, mounted, accessible, device, device_attached).
  type: dict
after:
  description: The same after the change (re-read), or the same as before when nothing was done.
  type: dict
changes:
  description: What changed (or, in check mode, would change).
  type: list
"""

try:
    from pyVmomi import vim
except ImportError:
    pass

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.common.text.converters import to_native
from ansible_collections.community.vmware.plugins.module_utils.vmware import PyVmomi, vmware_argument_spec


class EsxVmfsVolume(PyVmomi):
    def __init__(self, module):
        super(EsxVmfsVolume, self).__init__(module)
        hosts = self.get_all_host_objs(esxi_host_name=module.params["esxi_hostname"])
        if not hosts:
            module.fail_json(msg="host %s not found" % module.params["esxi_hostname"])
        self.host = hosts[0]
        self.ss = self.host.configManager.storageSystem

    def read(self):
        uuid = self.module.params["vmfs_uuid"]
        found = None
        for mi in self.ss.fileSystemVolumeInfo.mountInfo or []:
            vol = mi.volume
            if isinstance(vol, vim.host.VmfsVolume) and vol.uuid == uuid:
                found = (mi, vol)
                break
        if found is None:
            return None
        mi, vol = found
        extents = [e.diskName for e in (vol.extent or [])]
        lun = None
        for l in self.ss.storageDeviceInfo.scsiLun or []:
            if extents and l.canonicalName == extents[0]:
                lun = l
        attached = None
        if lun is not None:
            attached = "off" not in (lun.operationalState or [])
        return {
            "name": vol.name,
            "mounted": bool(mi.mountInfo.mounted),
            "accessible": bool(mi.mountInfo.accessible),
            "extents": extents,
            "snapshot": bool(getattr(vol, "forceMountedInfo", None)),
            "device_attached": attached,
            "lun_uuid": lun.uuid if lun is not None else None,
        }

    def lun(self, device):
        for l in self.ss.storageDeviceInfo.scsiLun or []:
            if l.canonicalName == device:
                return l
        return None

    def run(self):
        p = self.module.params
        before = self.read()
        if before is None and p["state"] in ("detached", "forgotten"):
            lun = self.lun(p["device"])
            attached = lun is not None and "off" not in (lun.operationalState or [])
            if attached:
                self.module.fail_json(msg="%s is attached on %s but carries no VMFS %s: refusing"
                                      % (p["device"], p["esxi_hostname"], p["vmfs_uuid"]))
            state_now = {"device": p["device"], "device_known": lun is not None, "device_attached": False}
            changes = []
            if p["state"] == "forgotten":
                if lun is not None:
                    self.module.fail_json(msg="%s is still known on %s (detached but present on the array?): disconnect "
                                          "it on the array and rescan first" % (p["device"], p["esxi_hostname"]), before=state_now)
                changes.append("forget the detached device %s on %s" % (p["device"], p["esxi_hostname"]))
                if not self.module.check_mode:
                    try:
                        self.ss.DeleteScsiLunState(lunCanonicalName=p["device"])
                    except vim.fault.NotFound:
                        changes = []            # no persistent entry: nothing to forget
            self.module.exit_json(changed=bool(changes), before=state_now, after=state_now, changes=changes)
        if before is not None and p["state"] == "forgotten":
            self.module.fail_json(msg="VMFS %s is still known on %s: detach it (state=detached), disconnect it on the "
                                  "array and rescan before forgetting the device" % (p["vmfs_uuid"], p["esxi_hostname"]),
                                  before=before)
        if before is None:
            self.module.fail_json(msg="host %s knows no VMFS volume %s (not rescanned, detached, or another UUID)"
                                  % (p["esxi_hostname"], p["vmfs_uuid"]))
        if before["extents"] != [p["device"]]:
            self.module.fail_json(msg="VMFS %s on %s is on %s, not on %s: refusing"
                                  % (p["vmfs_uuid"], p["esxi_hostname"], before["extents"], p["device"]), before=before)
        if before["snapshot"]:
            self.module.fail_json(msg="VMFS %s on %s is a force-mounted snapshot copy: refusing" % (p["vmfs_uuid"], p["esxi_hostname"]),
                                  before=before)
        changes = []
        state = p["state"]
        try:
            if state == "mounted" and not before["mounted"]:
                changes.append("mount %s (%s) on %s" % (before["name"], p["vmfs_uuid"], p["esxi_hostname"]))
                if not self.module.check_mode:
                    self.ss.MountVmfsVolume(vmfsUuid=p["vmfs_uuid"])
            if state in ("unmounted", "detached") and before["mounted"]:
                changes.append("unmount %s (%s) on %s" % (before["name"], p["vmfs_uuid"], p["esxi_hostname"]))
                if not self.module.check_mode:
                    self.ss.UnmountVmfsVolume(vmfsUuid=p["vmfs_uuid"])
            if state == "detached" and before["device_attached"]:
                changes.append("detach %s on %s" % (p["device"], p["esxi_hostname"]))
                if not self.module.check_mode:
                    self.ss.DetachScsiLun(lunUuid=before["lun_uuid"])
        except vim.fault.ResourceInUse as e:
            self.module.fail_json(msg="%s on %s is in use (VMs registered on it?): %s"
                                  % (before["name"], p["esxi_hostname"], to_native(e.msg)), before=before)
        except (vim.fault.VimFault, vim.fault.HostConfigFault) as e:
            self.module.fail_json(msg="%s on %s: %s" % (before["name"], p["esxi_hostname"], to_native(getattr(e, "msg", e))),
                                  before=before, changes=changes)
        after = before
        if changes and not self.module.check_mode:
            self.ss.RefreshStorageSystem()
            after = self.read() or {"gone": True}
        self.module.exit_json(changed=bool(changes), before=before, after=after, changes=changes)


def main():
    spec = vmware_argument_spec()
    spec.update(esxi_hostname=dict(type="str", required=True), vmfs_uuid=dict(type="str", required=True),
                device=dict(type="str", required=True),
                state=dict(type="str", required=True, choices=["mounted", "unmounted", "detached", "forgotten"]))
    module = AnsibleModule(argument_spec=spec, supports_check_mode=True)
    EsxVmfsVolume(module).run()


if __name__ == "__main__":
    main()
