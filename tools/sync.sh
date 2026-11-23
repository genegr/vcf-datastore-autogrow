#!/usr/bin/env bash
# Copy the working tree from the workstation (Git Bash) to the run host.
#   SYNC_HOST=user@run-host [SYNC_DIR=vsphere-dsgrow] bash tools/sync.sh      (or put them in .sync.env)
# Never copied: .git, .state/ (vault, env file), state/ (request files), .env (the run host's own), .venv. tar never deletes: after removing or
# renaming a file here, delete it on the run host too.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
env_host=${SYNC_HOST-} env_dir=${SYNC_DIR-} env_opts=${SYNC_SSH_OPTS-}
[[ -f $ROOT/.sync.env ]] && source "$ROOT/.sync.env"
SYNC_HOST=${env_host:-${SYNC_HOST-}} SYNC_DIR=${env_dir:-${SYNC_DIR-}} SYNC_SSH_OPTS=${env_opts:-${SYNC_SSH_OPTS-}}
: "${SYNC_HOST:?set SYNC_HOST=user@host (env or .sync.env)}"
SYNC_DIR=${SYNC_DIR:-vsphere-dsgrow}
# The running rulebook executes the code in $SYNC_DIR: never copy over a datastore that is in flight or waiting for a
# click (state/locks/). SYNC_FORCE=1 overrides, only with the owner's go-ahead. An unreachable host also stops here.
# shellcheck disable=SC2086
if [[ ${SYNC_FORCE-} != 1 ]]; then
  ssh ${SYNC_SSH_OPTS:-} "$SYNC_HOST" bash -s -- "$SYNC_DIR" < "$ROOT/tools/lock_check.sh" || {
    echo "not synced: a datastore is in flight or waiting for a click, or the host did not answer (SYNC_FORCE=1 overrides)" >&2
    exit 3
  }
fi
# shellcheck disable=SC2086
tar -C "$ROOT" -czf - \
  --exclude=./.git --exclude=./.state --exclude=./state --exclude=./.sync.env --exclude=./.env --exclude=./.venv \
  --exclude='*/__pycache__' --exclude=./.pytest_cache --exclude=./tests/rulebook/out . \
  | ssh ${SYNC_SSH_OPTS:-} "$SYNC_HOST" "mkdir -p '$SYNC_DIR' && tar -xzf - -C '$SYNC_DIR'"
echo "synced to $SYNC_HOST:$SYNC_DIR"
