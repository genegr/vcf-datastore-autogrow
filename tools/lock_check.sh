#!/usr/bin/env bash
# Streamed to the run host by tools/sync.sh (ssh host bash -s -- <sync dir> < tools/lock_check.sh); also runs anywhere
# for a directory that holds state/locks/. Exit 3 when a datastore lock says an expansion is in flight (executing),
# waiting for a click (proposed) or being removed by the demo (cleaning) and has not expired: the running rulebook
# executes the code in that directory, so a copy over it can change an approval's remaining steps.
# FAILS CLOSED: a lock file it cannot read (no status, a status in another case), or an in-flight one without a readable
# expiry, counts as in flight. An `executing` lock gets an hour of grace after its expiry (a run that began just before it).
# tests/unit/test_tools.py feeds it odd files; tests/offline/test_flow.py feeds it what the real playbooks wrote.
set -u
dir=${1:?usage: lock_check.sh <dir with state/locks>}
now=$(date +%s)
busy=0
shopt -s nullglob
for f in "$dir"/state/locks/*.json; do
  status=$(grep -o '"status": *"[A-Za-z]*"' "$f" | head -1 | cut -d'"' -f4 | tr 'A-Z' 'a-z')
  expires=$(grep -o '"expires": *"\{0,1\}[0-9]*' "$f" | head -1 | grep -o '[0-9]*$')
  grace=0
  [[ $status == executing ]] && grace=3600
  case $status in
    proposed|executing|cleaning)
      if [[ -z $expires ]] || [[ $((expires + grace)) -gt $now ]]; then
        echo "in flight: $(basename "$f") is $status (expires ${expires:-unreadable})" >&2
        busy=1
      fi ;;
    released|failed|cleared|denied|done|expired) ;;
    *)
      echo "unreadable lock: $(basename "$f") (status '${status}'): treated as in flight" >&2
      busy=1 ;;
  esac
done
exit $((busy ? 3 : 0))
