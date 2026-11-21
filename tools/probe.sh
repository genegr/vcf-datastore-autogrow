#!/usr/bin/env bash
# Run a READ-ONLY probe (tests/probes/*.yml) on the run host, in the dsgrow-eda image (DSGROW_IMAGE to override), with
# nested-vcf's vaults and records mounted read-only and dsgrow's own secrets (.state, vault id dsgrow) when present.
#   cd ~/vsphere-dsgrow && bash tools/probe.sh tests/probes/eui_serial.yml [ansible-playbook args]
# Writes only under ./.state/probes/.
set -euo pipefail
cd "$(dirname "$0")/.."
probe=${1:?usage: tools/probe.sh tests/probes/<probe>.yml [args]}; shift
case "$probe" in tests/probes/*.yml) ;; *) echo "only tests/probes/*.yml" >&2; exit 2 ;; esac
NVCF=${NVCF:-$HOME/nested-vcf}
IMAGE=${DSGROW_IMAGE:-dsgrow-eda:1}
mkdir -p .state/probes .state/home
vault_ids=(--vault-id nvcf@/nvcf/.state/vault-pass)
[[ -s .state/vault-pass ]] && vault_ids+=(--vault-id dsgrow@/work/.state/vault-pass)
exec docker run --rm --network host --user "$(id -u):$(id -g)" \
  -v "$PWD:/work" -v "$NVCF:/nvcf:ro" -w /work \
  -e HOME=/work/.state/home \
  -e ANSIBLE_FILTER_PLUGINS=/work/plugins/filter \
  -e ANSIBLE_LIBRARY=/work/plugins/modules \
  -e ANSIBLE_ROLES_PATH=/work/roles \
  -e ANSIBLE_STDOUT_CALLBACK=default -e ANSIBLE_CALLBACK_RESULT_FORMAT=yaml \
  "$IMAGE" ansible-playbook -i localhost, -c local "${vault_ids[@]}" "$probe" "$@"
