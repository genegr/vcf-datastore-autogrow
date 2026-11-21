---
name: vcf-ops-webhook
description: Use when creating or changing the VCF Operations side - symptom and alert definitions, the outbound webhook to EDA, its payload templates (new, updated, canceled), the notification rule, collection interval, or the test fixtures that mimic those payloads.
---

# VCF Operations → EDA webhook

VCF Ops is the only event source. It sends a small contract (`schema: dsgrow.vcfops.v1`); sizes, usage and backing
are looked up live by the playbooks, never taken from the payload.

## Objects (all created by `setup/vcfops.yml`; re-apply after each 90-day lab rebuild)

- Symptoms "dsgrow - datastore used >= 80 %" and "dsgrow - datastore time remaining under N days" on Datastore
  (VMWARE), one alert definition "dsgrow - datastore capacity" firing on either, enabled in every active policy.
- Outbound instance: Webhook Notification Plugin, `Url` = `http://<dsgrow_eda_host>:5080`, "No Credential"; the
  token is a header in the template.
- Payload template with all three entries (`newAlertTemplate`, `updatedAlertTemplate`, `canceledAlertTemplate`).
  Without the canceled one, Slack messages never clear. Exported copies: `vcf-ops/payload-templates/`.
- Notification rule: resource kind Datastore and only the definitions in `dsgrow_alert_definitions`.
- `setup/vcfops_interval.yml`: the demo vCenter adapter collects every 1 min instead of 5.

## Payload (same in all three templates except `event`)

```json
{"schema": "dsgrow.vcfops.v1", "event": "new", "alert_id": "${ALERT_ID}", "alert_definition": "${ALERT_DEFINITION}",
 "status": "${ALERT_STATUS}", "criticality": "${ALERT_CRITICALITY}", "resource_name": "${RESOURCE_NAME}",
 "resource_id": "${RESOURCE_ID}", "resource_kind": "${RESOURCE_KIND}", "adapter_kind": "${ADAPTER_KIND}",
 "create_time": "${CREATE_TIME}", "update_time": "${UPDATE_TIME}", "cancel_time": "${CANCEL_TIME}",
 "symptoms": "${SYMPTOMS}", "link": "${LINK_TO_ALERT}"}
```
Headers: `Content-Type: application/json`, `Authorization: Bearer <EDA token>`.

## suite-api facts (VCF Ops 9.1.1, read on the lab)

- `/api/alertplugins` (type `WebhookPlugin`), `/api/notifications/templates` (`formattingTemplate.type:
  WEBHOOK_TEMPLATE`, `endpoint` is appended to the instance `Url`), `/api/notifications/rules`,
  `/api/symptomdefinitions`, `/api/alertdefinitions`, `/api/policies`, `PUT /api/adapters` (`monitoringInterval`).
- Datastore metrics: `capacity|usedSpacePct`, `OnlineCapacityAnalytics|timeRemaining` (days, 366 = none),
  `diskspace|disktotal` / `diskused` (GB). Resource identity: `VMEntityObjectID` (moref) + `VMEntityVCID`.
- Stats queries need `intervalType` + `intervalQuantifier` and `rollUpType=MAX` (`NONE` is refused).

## Gotchas (measured)

- Names are stored HTML-escaped (`>=` → `&gt;=`): create-if-missing by name never matched and made duplicates.
  `setup/vcfops.yml` asserts names match `^[A-Za-z0-9 ._-]+$`.
- An API-created alert definition is enabled in no policy: enable it per active policy
  (`PUT /api/alertdefinitions/{id}/enable?policyId=…`); recorded in `.state/vcfops-alert-enabled.json`.
- Symptom condition `value` must be a string (`"80"`). A symptom on `timeRemaining` answers HTTP 500, so that
  trigger is off (`dsgrow_vcfops_time_symptom: false`) until its shape is known (create one in the UI, GET it).
- Deletes: `DELETE /api/symptomdefinitions/{id}` (`?id=` answers 405). Error details:
  `message` + `validationFailures[].failureMessage`; token calls are `no_log`.
- VCF Ops reads vCenter's cached datastore summary: guest writes don't move it. A change reaches VCF Ops only after
  the datastore's figures are refreshed (start.yml uses `dsgrow_datastore_refresh`).
- No `${ADAPTER_INSTANCE}` placeholder exists and `RESOURCE_NAME` isn't unique: the playbooks search every vCenter
  in `dsgrow_vcenters` and require exactly one match.
- Placeholders are substituted as text: keep each inside quotes; a malformed event simply doesn't match.
- `${SYMPTOMS}` may be "N/A" (KB 437925); `${LINK_TO_ALERT}` uses the node IP (KB 370239). Never parse either.
- The UI "Test" button sends a default payload the rulebook ignores; that's expected.

## Fixtures (`tests/fixtures/vcfops/`)

`new_datastore_80` (happy path), `updated_datastore_85` (same alert: throttled), `new_datastore_again` (new alert id,
same datastore: proposes), `no_alert_id` (ignored), `canceled_datastore`, `non_datastore_resource`,
`unknown_schema`, `symptoms_na` (still works), `approval_on_vcfops` (must match nothing).

A contract change updates the three templates, every fixture, the rulebook conditions and propose's input
validation together, and bumps the schema suffix.
