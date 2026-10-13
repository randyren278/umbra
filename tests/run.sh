#!/bin/zsh
# Runs all of Umbra's tests (make test runs this): builds build/Umbra.app if it is missing or
# stale, then runs smoke.sh and the end-to-end suite against it. Every run uses a throwaway
# UMBRA_PROFILE; the installed app and the default cookie store are never touched.
#   tests/run.sh [unittest args, e.g. -v or ReadTests]     UMBRA=/path/to/umbra tests/run.sh
set -u
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

print "\n== test_umbra.py"
start=$SECONDS
python3 $repo/tests/test_umbra.py "$@" 2>&1 | tee $log
suite=${pipestatus[1]}
suite_time=$((SECONDS - start))
ran=$(grep -Eo '^Ran [0-9]+ tests?' $log | grep -Eo '[0-9]+')
skipped=$(grep -Eo 'skipped=[0-9]+' $log | grep -Eo '[0-9]+')

print "\n== summary"
printf '%-24s %s (%.0fs)\n' smoke.sh "$( ((smoke)) && print FAILED || print passed)" $smoke_time
printf '%-24s %s, %s tests%s (%.0fs)\n' test_umbra.py "$( ((suite)) && print FAILED || print passed)" \
    "${ran:-?}" "${skipped:+, $skipped skipped}" $suite_time
printf 'total %.0fs\n' $SECONDS
(( smoke || suite )) && exit 1
exit 0
