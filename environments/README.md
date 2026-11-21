# Environments

Everything specific to one lab lives in `environments/<env>/`; the rest of the repository is generic. `.env`
(`DSGROW_ENV`) selects the environment for the tools and the services.

```
environments/<env>/
  inventory/
    hosts.yml
    group_vars/dsgrow/
      vcenters.yml       vCenters (dsgrow user, admin user, adapter instances)
      flasharrays.yml    arrays (Purity name, stable key, REST version)
      eda.yml            run host webhook, VCF Ops API URL, collection interval
      policy.yml         overrides of playbooks/group_vars/all/dsgrow.yml (e.g. the size step)
      demo.yml           the demo's objects (cluster, hosts, array, datastore, VM, template, network)
      lab.yml            how setup/, demo/ and probes reach the lab: admin secrets, SSH keys, jump host, OVA
      site.yml           the addresses (gitignored; template site.yml.example in example/)
  probes/                read-only probes that only make sense on this lab (bash tools/probe.sh ...)
  docs/                  this lab's plan and the history of every change made to it
```

- `example/`: the template. Copy it to `environments/<your env>/` and fill it in (README "Getting started").
- `vcf9-1/`: the reference lab the project was built and demonstrated on.
