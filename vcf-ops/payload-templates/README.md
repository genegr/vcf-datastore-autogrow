# VCF Operations payload templates (contract `dsgrow.vcfops.v1`)

One body per alert state. They are identical except for the literal `event` (skill `vcf-ops-webhook`):

| File | Template entry | Rulebook rule |
|---|---|---|
| `new.json` | `newAlertTemplate` | propose |
| `updated.json` | `updatedAlertTemplate` | propose (throttled per `resource_id` + `alert_id`) |
| `canceled.json` | `canceledAlertTemplate` | cleared |

Each entry sends `POST` to `http://<run host>:5080/endpoint` (`dsgrow_eda_host`) with these headers. The token value comes from
the dsgrow vault and is never written here:

```
Content-Type: application/json
Authorization: Bearer <DSGROW_EDA_VCFOPS_TOKEN>
```

`setup/vcfops.yml` creates the outbound instance, the template, the symptom and alert definitions and the
notification rule through the suite-api.

## Placeholders (read from VCF Ops 9.1.1 on the lab, 2026-11-19, `tests/probes/ops_read.yml`)

The shipped templates use `ALERT_ID`, `ALERT_DEFINITION`, `ALERT_STATUS`, `ALERT_CRITICALITY`, `RESOURCE_NAME`,
`RESOURCE_ID`, `RESOURCE_KIND`, `ADAPTER_KIND`, `CREATE_TIME`, `UPDATE_TIME`, `CANCEL_TIME`, `SYMPTOMS` and
`LINK_TO_ALERT`, all of which our contract uses. There is **no `${ADAPTER_INSTANCE}`**, so the payload doesn't name
the vCenter. The playbooks search every configured vCenter for the datastore and require exactly one match
(`roles/dsgrow_resolve/tasks/pick_vcenter.yml`). They still accept an `adapter_instance` field if a later version
adds one. Format (from "Default Webhook Template"): `formattingTemplate.type: WEBHOOK_TEMPLATE`, one entry per state
with `endpoint`, `headers` as `{key, value}`, `method`, `payload` (a string).

- `${SYMPTOMS}` can be `N/A` (Broadcom KB 437925). It is informational only.
- `${LINK_TO_ALERT}` may carry the node IP (KB 370239). The Slack message links it only if it starts with `http`.
- Placeholders are substituted as text, so every one stays inside double quotes.
