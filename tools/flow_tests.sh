#!/usr/bin/env bash
# The offline flow tests (tests/offline/test_flow.py: the REAL propose / execute / deny / cleared playbooks against a
# fake FlashArray, vCenter and Slack) split over PAR containers (default 4; each has its own /tmp, fake world and Slack
# port), with the settings the rulebook service runs under in compose.yaml: the checkout read-only, .state hidden behind
# a tmpfs, no capability, no network. About 3 minutes instead of 10. Touches no real system.
#   cd ~/vsphere-dsgrow && bash tools/flow_tests.sh        (PAR=2 to use fewer containers)
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=${DSGROW_IMAGE:-dsgrow-eda:1}
PAR=${PAR:-4}
mkdir -p .state                      # the tmpfs below needs its mount point in the read-only checkout
args=$(mktemp -d)
trap 'rm -rf "$args"' EXIT

run() {
  docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true \
    --user "$(id -u):$(id -g)" -v "$PWD:/work:ro" --tmpfs /work/.state -v "$args:/args:ro" \
    -e HOME=/tmp -w /work "$IMAGE" "$@"
}

n=0
while IFS= read -r id; do
  [[ -n $id ]] || continue
  echo "$id" >> "$args/g$((n % PAR))"
  n=$((n + 1))
done < <(run python3 -m pytest --collect-only -q -p no:cacheprovider tests/offline/test_flow.py | grep '::')
[[ $n -gt 0 ]] || { echo "no flow tests collected" >&2; exit 1; }
echo "flow tests: $n over $PAR containers"

for g in "$args"/g*; do
  ( run python3 -m pytest -q -p no:cacheprovider -rfE "@/args/$(basename "$g")" > "$g.out" 2>&1; echo $? > "$g.rc" ) &
done
wait
rc=0
for out in "$args"/g*.out; do echo "---- $(basename "$out" .out)"; tail -n "${TAILN:-20}" "$out"; done
for r in "$args"/*.rc; do [[ $(cat "$r") == 0 ]] || rc=1; done
# a skipped flow test means ansible-playbook was not there: nothing was checked, which is not a pass
if grep -qE "[0-9]+ skipped" "$args"/g*.out; then echo "flow tests were SKIPPED: this image has no ansible-playbook?" >&2; rc=1; fi
exit $rc
