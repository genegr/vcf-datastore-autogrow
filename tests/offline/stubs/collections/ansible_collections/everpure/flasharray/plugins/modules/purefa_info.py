#!/usr/bin/python
# TEST STUB of everpure.flasharray.purefa_info (read only) for tests/offline/test_flow.py: answers from the fake world.
from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec=dict(fa_url=dict(required=True), api_token=dict(required=True, no_log=True),
                           gather_subset=dict(type="list", elements="str", default=["minimum"])),
        supports_check_mode=True)
    path = os.environ["DSGROW_FAKE_WORLD"]
    with open(path, encoding="utf-8") as fh:
        world = json.load(fh)
    array = world["arrays"][module.params["fa_url"]]
    subset = module.params["gather_subset"]
    # a rename after the preflight: the read right before the write (volumes only) finds the name gone
    if world.get("rename_before_write") and subset == ["volumes"]:
        for name in list(array["volumes"]):
            array["volumes"]["renamed-by-someone-else"] = array["volumes"].pop(name)
            break
    world["calls"].append({"module": "purefa_info", "array": module.params["fa_url"], "subset": subset})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(world, fh, indent=1)
    info = {}
    if "volumes" in subset:
        info["volumes"] = {n: {"serial": v["serial"], "size": v["size"], "hosts": [], "host_groups": []}
                           for n, v in array["volumes"].items() if not v.get("destroyed")}
    if "capacity" in subset:
        info["capacity"] = dict(array["capacity"])
    module.exit_json(changed=False, purefa_info=info)


if __name__ == "__main__":
    main()
