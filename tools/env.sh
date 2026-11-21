# Sourced by tools/setup.sh and tools/probe.sh: the environment (.env, see .env.example) as docker and
# ansible-playbook arguments.
#   DSGROW_ENV             the inventory, environments/<DSGROW_ENV>/inventory/ (required)
#   DSGROW_LAB_DIR         optional, mounted read-only at /lab (the lab's admin vault, SSH keys, OVA: lab.yml)
#   DSGROW_LAB_VAULT_PASS  optional, the lab vault's password file (path inside the container)
[[ -f .env ]] && { set -a; source ./.env; set +a; }
: "${DSGROW_ENV:?set DSGROW_ENV in .env (see .env.example)}"
[[ -d environments/$DSGROW_ENV/inventory ]] || { echo "no environments/$DSGROW_ENV/inventory" >&2; exit 2; }
IMAGE=${DSGROW_IMAGE:-dsgrow-eda:1}
lab_mount=()
[[ -n ${DSGROW_LAB_DIR:-} ]] && lab_mount=(-v "$DSGROW_LAB_DIR:/lab:ro")
vault_ids=()
[[ -n ${DSGROW_LAB_VAULT_PASS:-} ]] && vault_ids+=(--vault-id "lab@$DSGROW_LAB_VAULT_PASS")
[[ -s .state/vault-pass ]] && vault_ids+=(--vault-id dsgrow@/work/.state/vault-pass)
env_vars=(-e "dsgrow_env=$DSGROW_ENV")
