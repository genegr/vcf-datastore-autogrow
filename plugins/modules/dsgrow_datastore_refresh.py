#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Refresh one datastore's space figures in vCenter (Datastore.RefreshDatastoreStorageInfo), nothing else."""
from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: dsgrow_datastore_refresh
short_description: Refresh a datastore's capacity, free space and usage in vCenter, without touching any host
description:
  - Calls Datastore.RefreshDatastoreStorageInfo on the one datastore with the given name, then reports its capacity
    and used space. It is datastore-scoped and synchronous. No host task is created, nothing is rescanned (HBA or
    VMFS), and no configuration changes.
  - It replaces vmware_host_scanhba refresh_storage, which refreshes each host's whole storage system and shows as a
    "Refresh host storage system" task on every host, wherever only the used space has to be current (owner
    2026-11-21, skill vmfs-datastore-expand "When to rescan").
  - In check mode the refresh is skipped and the cached figures are reported.
options:
  datastore:
    description: Datastore name. Exactly one datastore with this name must exist, else the module fails.
    type: str
    required: true
  datacenter:
    description: Limit the search to this datacenter.
    type: str
extends_documentation_fragment:
  - vmware.vmware.base_options
"""

RETURN = r"""
refreshed:
  description: Whether the refresh was called (false in check mode).
  type: bool
datastore:
  description: The datastore after the refresh.
  type: dict
  contains:
    name: {type: str, description: Datastore name.}
    moid: {type: str, description: The datastore's managed object id.}
    capacity_bytes: {type: int, description: Capacity as vCenter reports it.}
    free_bytes: {type: int, description: Free space as vCenter reports it.}
    used_bytes: {type: int, description: Capacity minus free space.}
"""

try:
    from pyVmomi import vim
except ImportError:  # handled by PyVmomi
    pass

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.community.vmware.plugins.module_utils.vmware import PyVmomi, vmware_argument_spec


class DatastoreRefresh(PyVmomi):
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
        if len(found) != 1:
            self.module.fail_json(msg="%d datastores are named %s" % (len(found), name), matches=len(found))
        ds = found[0]
        refreshed = False
        if not self.module.check_mode:
            ds.RefreshDatastoreStorageInfo()
            refreshed = True
        summary = ds.summary
        capacity, free = int(summary.capacity or 0), int(summary.freeSpace or 0)
        self.module.exit_json(changed=False, refreshed=refreshed, datastore={
            "name": ds.name, "moid": ds._moId, "capacity_bytes": capacity, "free_bytes": free,
            "used_bytes": capacity - free})


def main():
    spec = vmware_argument_spec()
    spec.update(datastore=dict(type="str", required=True), datacenter=dict(type="str"))
    module = AnsibleModule(argument_spec=spec, supports_check_mode=True)
    DatastoreRefresh(module).run()


if __name__ == "__main__":
    main()
