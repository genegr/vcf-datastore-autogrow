---
name: slack-approval-bridge
description: Use when designing the Slack approval message, building or changing the Slack bridge app that turns a button click into a signed approval event for the rulebook, or handling Slack authz, modals and message updates.
---

# Slack approval message and bridge

The bridge is the approval gate: the only sender of `dsgrow.approval.v1` events to the rulebook's `approvals`
source, and only after an authorised human clicked.

## Slack app (`slack/manifest.yml`)

- Socket Mode: an outbound WebSocket, nothing exposed. App-level token with `connections:write`.
- Bot scope `chat:write` only; the approver's name comes from the click payload. Invite the bot to the channel.
- Free workspace, no user groups: approvers are Slack member IDs in `DSGROW_APPROVER_IDS` (same list as
  `dsgrow_approver_ids`). In HTTP mode every request would need Slack signature verification; Bolt does it.

## Proposal message (`roles/dsgrow_slack/templates/proposal.json.j2`, posted by propose.yml)

Header (datastore, used %), fields (vCenter, used/capacity, hosts, FlashArray volume, array free), the proposed
sizes with growth, used % after and array free after, then one button per size, "Custom…", "Deny", the alert link,
request id and expiry.

Sizes: for each `u` in `dsgrow_target_utilization_options`, `target = roundup(used / u, dsgrow_size_step_bytes)`;
shown only if target > current and every cap holds; duplicates once. No option: say why, offer Deny only.

Size buttons (owner 2026-11-20): label "Grow to 272 GiB (→ 62.4%)", **no `style`** (a green default drew clicks),
and a `confirm` dialog naming the size ("Grow to X?", confirm "Grow to X", deny "Cancel"). Button `value` (≤ 2000
chars) is compact JSON `{"rid","vc","ds","dev","cur","tgt","exp"}`. `tests/offline/test_proposal.py` checks every
rendered button parses in the bridge.

## Handler order (`bridge/actions.py`)

1. `ack()` at once (3 s). For "Custom…", `views.open` with the `trigger_id` first (it expires in 3 s).
2. Authorise against the allow-list; others get an ephemeral refusal.
3. Validate: not expired, size parses, multiple of the step, above current, within caps (a copy for fast feedback;
   execute is the authority). A label whose size differs from the carried size is refused.
4. Claim the request (a second click within the claim window does nothing), `chat.update` without buttons.
5. POST the event, body signed as sent:
   `POST http://127.0.0.1:5081/approval`, `Authorization: Bearer <approvals token>`,
   `x-dsgrow-signature: sha256=<hex HMAC-SHA256(secret, body)>`, body
   `{"schema":"dsgrow.approval.v1","decision":"approve","rid","vc","ds","dev","tgt","approver_id","approver_name","channel","ts","sent_at"}`.
6. Log action_id + label + bytes; post "approved" in the thread quoting the button label. On a send failure,
   restore the buttons and post the error. Deny: steps 1, 2, 4 with "Denied", no event.

## Code

`app.py` (Bolt wiring), `actions.py`, `validate.py` (pure parsing and caps), `rulebook_client.py` (signed POST,
stdlib), `messages.py`, `config.py` (env: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `DSGROW_APPROVER_IDS`,
`DSGROW_APPROVALS_URL`, `DSGROW_EDA_APPROVALS_TOKEN`, `DSGROW_APPROVAL_HMAC_SECRET`, caps incl.
`DSGROW_SIZE_STEP_BYTES`, which must equal the inventory step). The image bakes the code in: rebuild after changes.

Tests (`bridge/tests`): unauthorised, expired, below current, over cap, off-step, double click, send failure restores
buttons, deny sends nothing, signature matches the rulebook's, label/value mismatch, no token in logs.

## Never

Send anywhere but the `approvals` source or with another schema; accept an unchecked size; log tokens or headers;
act before `ack()`.
