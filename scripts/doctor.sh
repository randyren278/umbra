#!/bin/zsh
# Checks what Umbra needs: macOS 14 or later, swiftc, the link `make install` made, a binary that
# runs and a valid sites file. Prints no cookies (it never runs `umbra cookies`) and no file contents.
# BINDIR, UMBRA_SITES and UMBRA_PROFILE are honoured as they are by make and umbra.
set -u
link=${BINDIR:-$HOME/.local/bin}/umbra
sites=${UMBRA_SITES:-$HOME/.config/umbra/sites.json}

failures=0
report() {  # report <status> <message>
    if (( $1 )); then
        print -u2 "FAIL $2"
        failures=$((failures + 1))
    else
        print "ok   $2"
    fi
}

macos=$(sw_vers -productVersion)
(( ${macos%%.*} >= 14 )); report $? "macOS $macos (needs 14 or later)"

swiftc=$(xcrun --find swiftc 2>/dev/null); report $? "swiftc ${swiftc:-missing: run xcode-select --install}"

target=$(readlink $link); report $? "$link -> ${target:-not a symlink: run make install}"

version=$($link --version 2>&1); report $? "umbra --version: ${version:-no output}"

if [[ -e $sites ]]; then
    error=$(python3 -m json.tool $sites 2>&1 >/dev/null); report $? "$sites ${error:+is not valid JSON: }${error:-is valid JSON}"
else
    print "ok   no sites file at $sites (optional: sign-in also takes URLs)"
fi

(( failures )) && { print -u2 "$failures failed"; exit 1 }
print "all good"
