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

# The only ansible-playbook options probe.sh and setup.sh pass through. None of them can name another playbook, an
# inventory, a connection or a vault file, so the path allow-list and the per-file --confirm gate cannot be bypassed
# by an extra argument ("bash tools/probe.sh tests/probes/x.yml setup/vcfops.yml" would run both).
dsgrow_check_args() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      -v|-vv|-vvv|-vvvv|--diff) shift ;;
      -e|--extra-vars)
        # only dsgrow's own variables (dsgrow_*, a probe's pr_*, the demo's dd_*): never ansible_* (connection, host,
        # interpreter, become), never @file or JSON
        [[ ${2-} =~ ^(dsgrow|pr|dd)_[A-Za-z0-9_]*=[A-Za-z0-9_.,:/+-]*$ ]] \
          || { echo "refused: $1 ${2-} (only -e dsgrow_*=value, pr_*=value or dd_*=value)" >&2; return 2; }
        shift 2 ;;
      -t|--tags|--skip-tags)
        [[ ${2-} =~ ^[A-Za-z0-9_,-]+$ ]] || { echo "refused: $1 ${2-}" >&2; return 2; }
        shift 2 ;;
      *) echo "refused argument: $1 (passed through: -v, --diff, -e name=value, --tags, --skip-tags)" >&2; return 2 ;;
    esac
  done
}
