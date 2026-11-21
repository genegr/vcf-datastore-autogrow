#!/usr/bin/env bash
# Offline checks on the run host with NO network (docker --network none), in the dsgrow-eda image: syntax of every playbook, the logic test (tests/offline/logic.yml), then the Python
# cross-check. Touches no real system.
#   cd ~/vsphere-dsgrow && bash tools/offline.sh
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=${DSGROW_IMAGE:-dsgrow-eda:1}
mkdir -p .state/offline
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD:/work:ro" \
  -v "$PWD/.state/offline:/tmp/dsgrow-offline" -w /work \
  -e HOME=/tmp -e ANSIBLE_CONFIG=/work/ansible.cfg -e ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections \
  -e DSGROW_STATE_DIR=/tmp/dsgrow-offline -e ANSIBLE_STDOUT_CALLBACK=default -e ANSIBLE_CALLBACK_RESULT_FORMAT=yaml \
  -e ANSIBLE_LOCAL_TEMP=/tmp/ansible-local -e ANSIBLE_REMOTE_TMP=/tmp/ansible-remote \
  --entrypoint sh "$IMAGE" -c '
    set -e
    for p in playbooks/*.yml setup/[!s]*.yml setup/secrets_init.yml demo/*.yml; do
      ansible-playbook --syntax-check -i localhost, "$p" >/dev/null 2>/tmp/err || { echo "SYNTAX $p"; cat /tmp/err; exit 1; }
    done
    echo "syntax: ok"
    ansible-playbook -i localhost, -c local tests/offline/logic.yml
    if python3 -c "import pytest" 2>/dev/null; then python3 -m pytest -q -p no:cacheprovider tests/offline; \
    else echo "(pytest not in this image: tests/offline/test_proposal.py skipped)"; fi'
