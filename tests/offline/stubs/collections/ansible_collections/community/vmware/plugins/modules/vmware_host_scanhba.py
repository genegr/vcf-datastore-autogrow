#!/usr/bin/python
# TEST STUB of community.vmware.vmware_host_scanhba for tests/offline/test_flow.py. An HBA rescan makes the host see
# the volume's current size; a storage refresh makes it see the datastore's current capacity. Recorded in world["calls"].
from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec=dict(
            hostname=dict(required=True), username=dict(required=True), password=dict(required=True, no_log=True),
            validate_certs=dict(type="bool", default=True), esxi_hostname=dict(required=True),
            rescan_hba=dict(type="bool", default=False), rescan_vmfs=dict(type="bool", default=False),
            refresh_storage=dict(type="bool", default=False)),
        supports_check_mode=True)
    path = os.environ["DSGROW_FAKE_WORLD"]
    with open(path, encoding="utf-8") as fh:
        world = json.load(fh)
    vc, p = world["vc"], module.params
    host = next(h for h in vc["hosts"] if h["name"] == p["esxi_hostname"])
    if not module.check_mode:
        if p["rescan_hba"]:
            host["device_size"] = world["arrays"][vc["array"]]["volumes"][vc["volume"]]["size"]
        if p["refresh_storage"]:
            host["vmfs_capacity"] = vc["vmfs_capacity"]
    world["calls"].append({"module": "vmware_host_scanhba", "host": p["esxi_hostname"], "rescan_hba": p["rescan_hba"],
                           "rescan_vmfs": p["rescan_vmfs"], "refresh_storage": p["refresh_storage"]})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(world, fh, indent=1)
    module.exit_json(changed=True)


if __name__ == "__main__":
    main()
