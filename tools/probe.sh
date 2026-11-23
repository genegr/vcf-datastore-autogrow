#!/usr/bin/env bash
# Run a READ-ONLY probe (tests/probes/*.yml: general; environments/<env>/probes/*.yml: one lab's investigations) on the run host, in the dsgrow-eda image (DSGROW_IMAGE to override), with
# the lab's files read-only at /lab (.env) and dsgrow's own secrets (.state, vault id dsgrow) when present.
#   cd ~/vsphere-dsgrow && bash tools/probe.sh tests/probes/demo_status.yml [ansible-playbook args]
# Writes only under ./.state/probes/.
set -euo pipefail
cd "$(dirname "$0")/.."
probe=${1:?usage: tools/probe.sh tests/probes/<probe>.yml|environments/<env>/probes/<probe>.yml [args]}; shift
# Exactly <dir>/<name>.yml, one level, no "..": a glob like tests/probes/*.yml also matches tests/probes/../../setup/x.yml
name='[A-Za-z0-9_][A-Za-z0-9_.-]*\.yml'
[[ $probe =~ ^tests/probes/$name$ || $probe =~ ^environments/[A-Za-z0-9_][A-Za-z0-9_-]*/probes/$name$ ]] \
  || { echo "only tests/probes/<name>.yml or environments/<env>/probes/<name>.yml" >&2; exit 2; }
[[ -f $probe && ! -L $probe ]] || { echo "no such probe file (or a symlink): $probe" >&2; exit 2; }
source tools/env.sh
dsgrow_check_args "$@" || exit 2
mkdir -p .state/probes .state/home
exec docker run --rm --network host --user "$(id -u):$(id -g)" \
  -v "$PWD:/work" ${lab_mount[@]+"${lab_mount[@]}"} -w /work \
  -e HOME=/work/.state/home \
  -e ANSIBLE_FILTER_PLUGINS=/work/plugins/filter \
  -e ANSIBLE_LIBRARY=/work/plugins/modules \
  -e ANSIBLE_ROLES_PATH=/work/roles \
  -e ANSIBLE_STDOUT_CALLBACK=default -e ANSIBLE_CALLBACK_RESULT_FORMAT=yaml \
  "$IMAGE" ansible-playbook -i localhost, -c local ${vault_ids[@]+"${vault_ids[@]}"} "${env_vars[@]}" "$probe" "$@"
