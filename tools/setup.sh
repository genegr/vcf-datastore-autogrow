#!/usr/bin/env bash
# Run a setup/ or demo/ playbook on the run host, in the dsgrow-eda image (everpure.flasharray with the #1103 fix,
# community.vmware, ssh), with nested-vcf's vaults read-only and dsgrow's own secrets in ./.state.
# setup/vcenter_account.yml needs govc: DSGROW_IMAGE=nested-vcf-toolbox:1 (it has no FlashArray task).
# THE GATE (CLAUDE.md rule 10): without --confirm <playbook file name> the run is forced to --check --diff.
#   bash tools/setup.sh setup/vcfops.yml                       dry run
#   bash tools/setup.sh setup/vcfops.yml --confirm vcfops.yml  the real change (only after the owner's go-ahead)
set -euo pipefail
cd "$(dirname "$0")/.."
pb=${1:?usage: tools/setup.sh setup/<step>.yml|demo/<step>.yml [--confirm <file name>] [ansible-playbook args]}; shift
case "$pb" in setup/*.yml|demo/*.yml) ;; *) echo "only setup/*.yml or demo/*.yml" >&2; exit 2 ;; esac
confirmed=no; args=()
while [[ $# -gt 0 ]]; do
  if [[ $1 == --confirm ]]; then
    [[ ${2-} == "$(basename "$pb")" ]] || { echo "--confirm must name $(basename "$pb")" >&2; exit 2; }
    confirmed=yes; shift 2
  else args+=("$1"); shift; fi
done
[[ $confirmed == yes ]] || args+=(--check --diff)
[[ -s .state/vault-pass ]] || { echo ".state/vault-pass is missing (setup/README.md step 0)" >&2; exit 2; }
NVCF=${NVCF:-$HOME/nested-vcf}
IMAGE=${DSGROW_IMAGE:-dsgrow-eda:1}
mkdir -p .state/home .state/secrets .state/logs
# demo/ posts to Slack as the rulebook does: the same environment (SLACK_BOT_TOKEN, DSGROW_SLACK_CHANNEL, tokens)
env_args=()
case "$pb" in demo/*) [[ -s .state/eda.env ]] && env_args=(--env-file .state/eda.env) ;; esac
log=.state/logs/$(basename "$pb" .yml)-$(date -u +%Y%m%dT%H%M%SZ)$([[ $confirmed == yes ]] || echo -check).log
echo "mode: $([[ $confirmed == yes ]] && echo CHANGE || echo check) -> $log"
docker run --rm --network host --user "$(id -u):$(id -g)" ${env_args[@]+"${env_args[@]}"} \
  -v "$PWD:/work" -v "$NVCF:/nvcf:ro" -w /work \
  -e HOME=/work/.state/home \
  -e ANSIBLE_FILTER_PLUGINS=/work/plugins/filter -e ANSIBLE_LIBRARY=/work/plugins/modules \
  -e ANSIBLE_ROLES_PATH=/work/roles \
  -e ANSIBLE_STDOUT_CALLBACK=default -e ANSIBLE_CALLBACK_RESULT_FORMAT=yaml \
  "$IMAGE" ansible-playbook -i localhost, -c local \
  --vault-id nvcf@/nvcf/.state/vault-pass --vault-id dsgrow@/work/.state/vault-pass \
  "$pb" ${args[@]+"${args[@]}"} 2>&1 | tee "$log"
exit "${PIPESTATUS[0]}"
