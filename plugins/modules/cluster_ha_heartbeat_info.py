#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copied unchanged from nested-vcf plugins/modules/ (2026-11-20) for the demo lifecycle (demo/); keep in sync.
# Our own module (read-only): no module of the pinned collections reads vSphere HA's heartbeat datastores
# (ClusterComputeResource.RetrieveDasAdvancedRuntimeInfo; community.vmware 6.3.0 / vmware.vmware 2.10.0 searched
# 2026-11-20). Why it exists: after the DR demo of 2026-11-10, HA's only heartbeat datastore on w01 is the demo's
# vcf9-1-dstest01; it may only be removed once HA heartbeats on other datastores (playbooks/w01_dr_repair.yml).

DOCUMENTATION = r"""
module: cluster_ha_heartbeat_info
short_description: Read vSphere HA's heartbeat datastores and each host's HA agent state for one cluster
description:
  - Read-only. Returns, per heartbeat datastore, the hosts using it (RetrieveDasAdvancedRuntimeInfo
    heartbeatDatastoreInfo), and each host's runtime.dasHostState.state.
options:
  cluster:
    description: Cluster name.
    type: str
    required: true
extends_documentation_fragment: [vmware.vmware.base_options]
"""

RETURN = r"""
heartbeat_datastores:
  description: Heartbeat datastore name -> host names using it.
  type: dict
das_states:
  description: Host name -> HA agent state (master, connectedToMaster, ...).
  type: dict
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.community.vmware.plugins.module_utils.vmware import PyVmomi, vmware_argument_spec


class HaHeartbeatInfo(PyVmomi):
    def run(self):
        cl = self.find_cluster_by_name(self.module.params["cluster"])
        if cl is None:
            self.module.fail_json(msg="cluster %s not found" % self.module.params["cluster"])
        info = cl.RetrieveDasAdvancedRuntimeInfo()
        hb = {}
        for d in getattr(info, "heartbeatDatastoreInfo", None) or []:
            hb[d.datastore.name] = sorted(h.name for h in (d.hosts or []))
        states = {h.name: (h.runtime.dasHostState.state if h.runtime.dasHostState else None) for h in cl.host}
        self.module.exit_json(changed=False, heartbeat_datastores=hb, das_states=states)


def main():
    spec = vmware_argument_spec()
    spec.update(cluster=dict(type="str", required=True))
    module = AnsibleModule(argument_spec=spec, supports_check_mode=True)
    HaHeartbeatInfo(module).run()


if __name__ == "__main__":
    main()
