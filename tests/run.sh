#!/bin/zsh
# Runs all of Umbra's tests (make test runs this): builds build/Umbra.app if it is missing or
# stale, then runs smoke.sh and the end-to-end suites (one-shots, daemon and sessions, MCP) against
# it. Every run uses throwaway profiles; the installed app and the default cookie store are never
# touched.
#   tests/run.sh [-v] [-k PATTERN]     UMBRA=/path/to/umbra tests/run.sh
set -u
failed=0
typeset -F SECONDS
repo=${0:A:h:h}
default=$repo/build/Umbra.app/Contents/MacOS/umbra
export UMBRA=${UMBRA:-$default}
if [[ $UMBRA == $default ]]; then
    make -C $repo -s build >/dev/null || { print -u2 "run.sh: make build failed"; exit 1 }
fi
log=$(mktemp)
trap 'rm -f $log' EXIT

print "== smoke.sh ($UMBRA)"
start=$SECONDS
$repo/tests/smoke.sh
smoke=$?
smoke_time=$((SECONDS - start))

results=()
for suite in test_umbra test_daemon mcp_conformance; do
    print "\n== $suite.py"
    start=$SECONDS
    python3 $repo/tests/$suite.py "$@" 2>&1 | tee $log
    code=${pipestatus[1]}
    ran=$(grep -Eo '^Ran [0-9]+ tests?' $log | grep -Eo '[0-9]+')
    skipped=$(grep -Eo 'skipped=[0-9]+' $log | grep -Eo '[0-9]+')
    results+=("$(printf '%-24s %s, %s tests%s (%.0fs)' $suite.py "$( ((code)) && print FAILED || print passed)" \
        "${ran:-?}" "${skipped:+, $skipped skipped}" $((SECONDS - start)))")
    (( code )) && failed=1
done

print "\n== summary"
printf '%-24s %s (%.0fs)\n' smoke.sh "$( ((smoke)) && print FAILED || print passed)" $smoke_time
print -l $results
printf 'total %.0fs\n' $SECONDS
(( smoke || failed )) && exit 1
exit 0
