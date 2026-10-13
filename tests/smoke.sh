#!/bin/zsh
# Runs every Umbra command against a local server and checks output and exit codes.
# Uses the installed binary and the real cookie store (only 127.0.0.1 is visited).
set -u
umbra=${UMBRA:-$HOME/.local/bin/umbra}
dir=$(mktemp -d)
trap 'kill $server 2>/dev/null; rm -rf $dir' EXIT

mkdir $dir/site
cat > $dir/site/index.html <<'EOF'
<!doctype html><title>Umbra fixture</title><p id=marker>in the shadow</p>
<script>setTimeout(() => { window.__answer = 'set later' }, 300)</script>
EOF
print -n '{"ok": true, "items": [1, 2, 3]}' > $dir/site/data.json
head -c 300000 /dev/urandom > $dir/site/blob.bin
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server $port --bind 127.0.0.1 --directory $dir/site >/dev/null 2>&1 &
server=$!
for _ in {1..50}; do curl -s -o /dev/null http://127.0.0.1:$port/ && break; sleep 0.1; done
base=http://127.0.0.1:$port

failures=0
check() {  # check <name> <expected exit> <actual exit> [<expected output> <actual output>]
    if [[ $2 != $3 || ( $# -eq 5 && $4 != $5 ) ]]; then
        print -u2 "FAIL $1: exit $3 (wanted $2)${5:+, output: ${5[1,200]}}"
        failures=$((failures + 1))
    else
        print "ok   $1"
    fi
}

out=$(print 'return document.title' | $umbra read $base/index.html); check "read returns the script's value" 0 $? "Umbra fixture" "$out"
out=$($umbra read $base/index.html < /dev/null); [[ $out == *'<p id="marker">in the shadow</p>'* ]]; check "read with no script prints the page" 0 $?
out=$(print 'await new Promise(r => setTimeout(r, 400)); return {late: true}' | $umbra read $base/index.html); check "read awaits and prints JSON" 0 $? '{"late":true}' "$out"
out=$(print 'while (!window.__answer) await new Promise(r => setTimeout(r, 100)); return window.__answer' | $umbra read $base/index.html); check "read waits for a page global" 0 $? "set later" "$out"
print 'throw new Error("boom")' | $umbra read $base/index.html 2>/dev/null; check "a throwing script exits 2" 2 $?
print 'return 1' | $umbra read --cookie no_such_cookie $base/index.html 2>/dev/null; check "a missing session cookie exits 3" 3 $?
print 'return 1' | $umbra read http://127.0.0.1:9/ 2>/dev/null; check "an unreachable page exits 4" 4 $?
print 'await new Promise(r => setTimeout(r, 5000))' | $umbra read --timeout 1 $base/index.html 2>/dev/null; check "a slow script exits 2 at the timeout" 2 $?

out=$($umbra fetch $base/data.json); check "fetch prints the body" 0 $? "$(cat $dir/site/data.json)" "$out"
out=$($umbra fetch $base/missing.json 2>/dev/null); code=$?; [[ $out == *404* ]]; check "an HTTP error exits 5 and still prints the body" "5 0" "$code $?"

$umbra download $base/blob.bin $dir/blob.bin && cmp -s $dir/site/blob.bin $dir/blob.bin; check "download saves the exact bytes" 0 $?
print stale > $dir/blob.bin
$umbra download $base/blob.bin $dir/blob.bin && cmp -s $dir/site/blob.bin $dir/blob.bin; check "download replaces an existing file" 0 $?
$umbra download $base/missing.bin $dir/missing.bin 2>/dev/null; check "a download HTTP error exits 5" "5 1" "$? $([[ -e $dir/missing.bin ]]; print $?)"

(print 'return "a"' | $umbra read $base/index.html > $dir/a) &
first=$!
(print 'return "b"' | $umbra read $base/index.html > $dir/b) &
wait $first $!
check "concurrent runs take turns" "a b" "$(cat $dir/a) $(cat $dir/b)"

$umbra frobnicate 2>/dev/null; check "an unknown command exits 64" 64 $?
[[ -d $HOME/Library/WebKit/com.randyren.umbra/WebsiteDataStore ]]; check "the store is keyed by the bundle identifier" 0 $?

(( failures )) && { print -u2 "$failures failed"; exit 1 }
print "all passed"
