#!/usr/bin/python
# TEST STUB of plugins/modules/dsgrow_datastore_backing.py for tests/offline/test_flow.py: answers from the fake world
# (a JSON file named by DSGROW_FAKE_WORLD) instead of a vCenter. It records every call in world["calls"].
from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec=dict(
            hostname=dict(required=True), username=dict(required=True), password=dict(required=True, no_log=True),
            validate_certs=dict(type="bool", default=True), datacenter=dict(), datastore=dict(required=True)),
        supports_check_mode=True)
    path = os.environ["DSGROW_FAKE_WORLD"]
    with open(path, encoding="utf-8") as fh:
        world = json.load(fh)
    vc = world["vc"]
    world["calls"].append({"module": "dsgrow_datastore_backing", "datastore": module.params["datastore"]})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(world, fh, indent=1)
    if vc["datastore"] != module.params["datastore"]:
        module.exit_json(changed=False, matches=0, datastore={})
    hosts = [{"name": h["name"], "mounted": h["mounted"], "accessible": h["accessible"],
              "devices": {vc["device"]: h["device_size"]}, "vmfs_capacity_bytes": h["vmfs_capacity"]}
             for h in vc["hosts"]]
    module.exit_json(changed=False, matches=1, datastore={
        "type": vc.get("type", "VMFS"), "vmfs_major_version": vc.get("vmfs_major_version", 6),
        "extents": vc.get("extents", [vc["device"]]), "maintenance_mode": "normal", "accessible": True,
        "capacity_bytes": vc["vmfs_capacity"], "used_bytes": vc["used"], "hosts": hosts})


if __name__ == "__main__":
    main()
