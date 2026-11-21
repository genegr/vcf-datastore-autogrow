#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copied unchanged from nested-vcf plugins/modules/ (2026-11-20) for the demo lifecycle (demo/); keep in sync.
# Our own module: no module of the pinned collections sets vSphere HA's heartbeat datastore preference
# (ClusterDasConfigInfo.hBDatastoreCandidatePolicy / heartbeatDatastore; community.vmware 6.3.0 vmware_cluster_ha and
# vmware.vmware 2.10.0 cluster_ha do not expose it; searched 2026-11-20). Why it exists: after the DR demo of 2026-11-10
# HA heartbeats only on the demo's vcf9-1-dstest01; before that datastore is removed, HA must prefer the domain's own
# datastores (playbooks/w01_dr_repair.yml, owner 2026-11-20).

DOCUMENTATION = r"""
module: cluster_ha_heartbeat_pref
short_description: Set vSphere HA's heartbeat datastore candidate policy and preferred datastores on a cluster
description:
  - Reads the cluster's dasConfig.hBDatastoreCandidatePolicy and heartbeatDatastore; when they differ from O(policy)
    and O(datastores), reconfigures the cluster (ReconfigureComputeResource_Task, modify=true, only these two fields).
  - Check mode reports what would change. Never touches any other HA, DRS or admission control setting.
options:
  cluster:
    description: Cluster name.
    type: str
    required: true
  datastores:
    description: The preferred heartbeat datastores (names; each must be mounted on the cluster's hosts).
    type: list
    elements: str
    required: true
  policy:
    description: The candidate policy.
    type: str
    choices: [allFeasibleDsWithUserPreference, userSelectedDs, allFeasibleDs]
    default: allFeasibleDsWithUserPreference
extends_documentation_fragment: [vmware.vmware.base_options]
"""

RETURN = r"""
before:
  description: Policy and preferred datastores before.
  type: dict
after:
  description: The same after the change (re-read).
  type: dict
"""

try:
    from pyVmomi import vim
except ImportError:
    pass

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.community.vmware.plugins.module_utils.vmware import PyVmomi, vmware_argument_spec, wait_for_task


class HaHeartbeatPref(PyVmomi):
    def state(self, cl):
        das = cl.configurationEx.dasConfig
        return {"policy": das.hBDatastoreCandidatePolicy, "datastores": sorted(d.name for d in (das.heartbeatDatastore or []))}

    def run(self):
        p = self.module.params
        cl = self.find_cluster_by_name(p["cluster"])
        if cl is None:
            self.module.fail_json(msg="cluster %s not found" % p["cluster"])
        before = self.state(cl)
        want = {"policy": p["policy"], "datastores": sorted(p["datastores"])}
        if before == want:
            self.module.exit_json(changed=False, before=before, after=before)
        ds_objs = []
        for name in p["datastores"]:
            ds = next((d for d in cl.datastore if d.name == name), None)
            if ds is None:
                self.module.fail_json(msg="datastore %s is not on cluster %s" % (name, p["cluster"]), before=before)
            ds_objs.append(ds)
        if self.module.check_mode:
            self.module.exit_json(changed=True, before=before, after=want)
        spec = vim.cluster.ConfigSpecEx(dasConfig=vim.cluster.DasConfigInfo(
            hBDatastoreCandidatePolicy=p["policy"], heartbeatDatastore=ds_objs))
        wait_for_task(cl.ReconfigureComputeResource_Task(spec=spec, modify=True))
        self.module.exit_json(changed=True, before=before, after=self.state(cl))


def main():
    spec = vmware_argument_spec()
    spec.update(cluster=dict(type="str", required=True), datastores=dict(type="list", elements="str", required=True),
                policy=dict(type="str", default="allFeasibleDsWithUserPreference",
                            choices=["allFeasibleDsWithUserPreference", "userSelectedDs", "allFeasibleDs"]))
    module = AnsibleModule(argument_spec=spec, supports_check_mode=True)
    HaHeartbeatPref(module).run()


if __name__ == "__main__":
    main()
