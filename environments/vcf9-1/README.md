# vcf9-1: the reference lab

A nested VCF 9.1.1 lab built and owned by the separate nested-vcf repo: two workload-domain vCenters (`w01`, `w02`),
ESXi hosts attached over NVMe/TCP to two FlashArrays (`z-fastaines-d`, `z-fastaines-f`), VCF Operations 9.1.1. The
demo runs on the `w02` domain (esx07-09, host group `vcf9-1-w02`).

Run host `.env`:

```
DSGROW_ENV=vcf9-1
DSGROW_LAB_DIR=<the nested-vcf checkout>          # mounted at /lab: its vaults, SSH keys and downloads
DSGROW_LAB_VAULT_PASS=/lab/.state/vault-pass
```

`inventory/group_vars/dsgrow/lab.yml` maps nested-vcf's vault variables (`vault_sso_admin_password`,
`vault_vcf_password`, `vault_array_tokens`) to dsgrow's and reaches the lab networks through the nested-vcf router
(`ssh_jump`).

Lab-specific notes:
- dsgrow never changes nested-vcf's objects; the demo only adds and removes its own volume's connection to the host
  group `vcf9-1-w02`. Accepted by the owner: nested-vcf's `wld_array` read-back fails while the demo volume exists.
- The arrays' `key` (`z-x90-a`, `z-x70-a`) is nested-vcf's alias for each array; it names the secrets.
- The lab is rebuilt every 90 days: re-run the setup steps listed in `setup/README.md` ("After a rebuild").

`docs/history.md` records every change made to this lab with its evidence; `docs/plan.md` is the original plan.
`probes/` holds the read-only investigations that established the facts the skills rely on (e.g. the NVMe EUI layout).
