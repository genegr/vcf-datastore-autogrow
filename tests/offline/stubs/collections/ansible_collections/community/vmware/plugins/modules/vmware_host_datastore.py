#!/usr/bin/python
# TEST STUB of community.vmware.vmware_host_datastore for tests/offline/test_flow.py. With auto_expand it grows the
# VMFS into the space its device has on the given host. Asked for another datastore or device it FAILS as the repo's
# role assumes the real module would create a VMFS there (the role asserts against that first). Recorded in world["calls"].
from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec=dict(
            hostname=dict(required=True), username=dict(required=True), password=dict(required=True, no_log=True),
            validate_certs=dict(type="bool", default=True), esxi_hostname=dict(required=True),
            datastore_name=dict(required=True), datastore_type=dict(required=True),
            vmfs_device_name=dict(required=True), vmfs_version=dict(type="int", default=6),
            auto_expand=dict(type="bool", default=False), state=dict(default="present")),
        supports_check_mode=True)
    path = os.environ["DSGROW_FAKE_WORLD"]
    with open(path, encoding="utf-8") as fh:
        world = json.load(fh)
    vc, p = world["vc"], module.params
    host = next(h for h in vc["hosts"] if h["name"] == p["esxi_hostname"])
    changed = False
    if p["datastore_name"] != vc["datastore"] or p["vmfs_device_name"] != vc["device"]:
        module.fail_json(msg="stub: this would create a VMFS on %s" % p["vmfs_device_name"])
    if p["auto_expand"] and host["device_size"] > vc["vmfs_capacity"]:
        changed = True
        if not module.check_mode:
            vc["vmfs_capacity"] = host["device_size"]
            host["vmfs_capacity"] = host["device_size"]
    world["calls"].append({"module": "vmware_host_datastore", "host": p["esxi_hostname"], "changed": changed})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(world, fh, indent=1)
    module.exit_json(changed=changed)


if __name__ == "__main__":
    main()
