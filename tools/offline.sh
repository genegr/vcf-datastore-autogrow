#!/usr/bin/env bash
# Offline checks on the run host with NO network (docker --network none), in the dsgrow-eda image: syntax of every playbook, the logic test (tests/offline/logic.yml), then the Python
# cross-check. Touches no real system.
#   cd ~/vsphere-dsgrow && bash tools/offline.sh
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=${DSGROW_IMAGE:-dsgrow-eda:1}
mkdir -p .state/offline
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true \
  --user "$(id -u):$(id -g)" -v "$PWD:/work:ro" \
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
    if python3 -c "import pytest" 2>/dev/null; then python3 -m pytest -q -p no:cacheprovider --ignore=tests/offline/test_flow.py tests/offline tests/unit bridge/tests; \
    else echo "(pytest not in this image: the Python tests are skipped)"; fi'

# The real rulebook against the fixtures (hostile events included), under the settings the rulebook SERVICE runs with
# (compose.yaml): the checkout read-only, .state hidden behind a tmpfs, no capability, no network.
docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  --user "$(id -u):$(id -g)" -v "$PWD:/work:ro" --tmpfs /work/.state -e HOME=/tmp -w /work \
  "$IMAGE" python3 -m pytest -q -p no:cacheprovider tests/rulebook

# The real propose / execute / deny / cleared playbooks against a fake world, the same way, in parallel containers
bash tools/flow_tests.sh

# The bridge image runs with a read-only root filesystem (compose.yaml: read_only: true). With no network it cannot
# reach Slack (Bolt's auth.test, then the WebSocket), so what this proves is that the image imports the code and builds
# the Bolt app under those settings without a write; the WebSocket run is proven by the deployment's read-back.
BRIDGE_IMAGE=${DSGROW_BRIDGE_IMAGE:-dsgrow-bridge:1}
if docker image inspect "$BRIDGE_IMAGE" >/dev/null 2>&1; then
  out=$(docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
    -e SLACK_BOT_TOKEN=xoxb-offline -e SLACK_APP_TOKEN=xapp-offline -e DSGROW_EDA_APPROVALS_TOKEN=offline \
    -e DSGROW_APPROVAL_HMAC_SECRET=offline -e DSGROW_APPROVER_IDS=U0OFFLINE0 \
    --entrypoint python "$BRIDGE_IMAGE" -c "import app, config; app.build_app(config.from_env(), token_verification=False); print('bridge app built')" 2>&1 || true)
  if grep -q "^bridge app built" <<<"$out" && ! grep -qiE "read-only file system|errno 30" <<<"$out"; then
    echo "bridge: the app builds with a read-only root filesystem"
  else
    echo "bridge: FAILED to build the app read-only:" >&2; echo "$out" | tail -8 >&2; exit 1
  fi
else
  echo "(bridge image $BRIDGE_IMAGE not built here: its read-only check is skipped)"
fi
