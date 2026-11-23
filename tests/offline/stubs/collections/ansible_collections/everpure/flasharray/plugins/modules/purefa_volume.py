#!/usr/bin/python
# TEST STUB of everpure.flasharray.purefa_volume for tests/offline/test_flow.py. Like the real module (1.46.0) it
# addresses the volume by NAME, only grows, honours check mode, and CREATES a volume when no live one has that name:
# the hazard the extend role guards against. Every call is recorded in world["calls"].
from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec=dict(fa_url=dict(required=True), api_token=dict(required=True, no_log=True),
                           name=dict(required=True), size=dict(required=True)),
        supports_check_mode=True)
    path = os.environ["DSGROW_FAKE_WORLD"]
    with open(path, encoding="utf-8") as fh:
        world = json.load(fh)
    array = world["arrays"][module.params["fa_url"]]
    name, size = module.params["name"], int(module.params["size"])
    changed, created = False, False
    vol = array["volumes"].get(name)
    if vol is None or vol.get("destroyed"):
        created, changed = True, True
        vol = {"serial": "FFFFFFFFFFFFFFFFFFFFFFFF", "size": size}
        if not module.check_mode:
            array["volumes"][name] = vol
    elif size > vol["size"]:
        changed = True
        if not module.check_mode:
            vol["size"] = size
    world["calls"].append({"module": "purefa_volume", "array": module.params["fa_url"], "name": name, "size": size,
                           "changed": changed, "created": created, "check_mode": module.check_mode})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(world, fh, indent=1)
    module.exit_json(changed=changed, volume={name: {"serial": vol["serial"], "size": vol["size"],
                                                     "destroyed": False}})


if __name__ == "__main__":
    main()
