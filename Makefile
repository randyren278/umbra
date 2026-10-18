# Builds Umbra.app, installs it to ~/Applications and links `umbra` into ~/.local/bin.
# APPDIR and BINDIR move the install (make install APPDIR=/tmp/a BINDIR=/tmp/b); `make help` lists targets.
# BUILD gives a parallel lane its own output (make build BUILD=build-x), signed the same way.
BUILD ?= build
APPDIR ?= $(HOME)/Applications
BINDIR ?= $(HOME)/.local/bin
APP := $(BUILD)/Umbra.app
BIN := $(APP)/Contents/MacOS/umbra
SIGNED := $(BUILD)/.signed
INSTALLED := $(APPDIR)/Umbra.app
LINK := $(BINDIR)/umbra
plist = $(shell /usr/libexec/PlistBuddy -c 'Print :$(1)' Info.plist)
ID := $(call plist,CFBundleIdentifier)
VERSION := $(call plist,CFBundleShortVersionString)
MINOS := $(call plist,LSMinimumSystemVersion)
ZIP := dist/Umbra-$(VERSION).zip

.PHONY: build install uninstall test icon release clean help

build: $(SIGNED)

# The bundle identifier is embedded in the binary as well, so the cookie store is the same
# however umbra is launched (from the app, a symlink or a copy). Without -target, swiftc would
# require the macOS it was built on rather than Info.plist's minimum.
$(BIN): $(wildcard Sources/*.swift) Info.plist Makefile
	mkdir -p $(@D)
	xcrun swiftc -O -swift-version 5 -target $(shell uname -m)-apple-macos$(MINOS) \
		-Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist \
		-o $@.tmp $(wildcard Sources/*.swift)
	mv $@.tmp $@

$(APP)/Contents/Info.plist: Info.plist
	mkdir -p $(@D)
	cp $< $@

$(APP)/Contents/Resources/AppIcon.icns: assets/AppIcon.icns
	mkdir -p $(@D)
	cp $< $@

# The linker signs only the binary, as "umbra.tmp". Signing the bundle seals Info.plist and the
# icon under the bundle identifier; the check stops the build if the identifier ever drifts.
$(SIGNED): $(BIN) $(APP)/Contents/Info.plist $(APP)/Contents/Resources/AppIcon.icns
	codesign --force --sign - --identifier $(ID) $(APP)
	codesign -dv $(APP) 2>&1 | grep -qxF 'Identifier=$(ID)' || { echo 'codesign: identifier is not $(ID)' >&2; exit 1; }
	touch $@

# Copies beside the installed app, then swaps it in by rename so a running job never sees half an app.
# The old app stops its daemon just before the swap, so the next command starts one from the new build.
install: build
	mkdir -p "$(APPDIR)" "$(BINDIR)"
	rm -rf "$(INSTALLED).new" "$(INSTALLED).old"
	ditto $(APP) "$(INSTALLED).new"
	if [ -x "$(INSTALLED)/Contents/MacOS/umbra" ]; then "$(INSTALLED)/Contents/MacOS/umbra" stop || true; fi
	if [ -d "$(INSTALLED)" ]; then mv "$(INSTALLED)" "$(INSTALLED).old"; fi
	mv "$(INSTALLED).new" "$(INSTALLED)"
	rm -rf "$(INSTALLED).old"
	ln -sfn "$(INSTALLED)/Contents/MacOS/umbra" "$(LINK)"

# Removes the app, the link and the keep-signed-in launch agents it made, nothing else: the cookie
# stores stay signed in. First it stops the daemon (UMBRA_PROFILE's, or the default one) so none
# keeps running from a deleted app.
uninstall:
	if [ -x "$(INSTALLED)/Contents/MacOS/umbra" ]; then "$(INSTALLED)/Contents/MacOS/umbra" stop || true; fi
	for agent in $(HOME)/Library/LaunchAgents/$(ID).keep-signed-in*.plist; do \
		[ -f "$$agent" ] || continue; launchctl bootout gui/$$(id -u)/$$(basename "$$agent" .plist) 2>/dev/null; rm -f "$$agent"; done
	rm -rf "$(INSTALLED)" "$(INSTALLED).new" "$(INSTALLED).old"
	if [ "$$(readlink "$(LINK)")" = "$(INSTALLED)/Contents/MacOS/umbra" ]; then rm "$(LINK)"; fi
	@echo 'Kept the cookie stores in $(HOME)/Library/WebKit/$(ID)/ (delete that folder to sign out everywhere).'

# Tests the build output, never the installed app.
test: build
	UMBRA=$(abspath $(BIN)) tests/run.sh

icon:
	python3 assets/build.py

release: $(ZIP).sha256

# No resource forks or extended attributes, so any unzip tool leaves the signature intact.
$(ZIP): $(SIGNED)
	mkdir -p $(@D)
	rm -f $@.tmp
	ditto -c -k --norsrc --noextattr --keepParent $(APP) $@.tmp
	mv $@.tmp $@

$(ZIP).sha256: $(ZIP)
	cd $(@D) && shasum -a 256 $(notdir $<) > $(notdir $@)

clean:
	rm -rf $(BUILD) dist

help:
	@echo 'make build      compile $(APP) and sign it ad hoc as $(ID)'
	@echo 'make test       run tests/run.sh against $(APP)'
	@echo 'make install    copy the app to $(APPDIR) and link umbra into $(BINDIR)'
	@echo 'make uninstall  remove that app and link (cookie stores are kept)'
	@echo 'make release    write $(ZIP) and $(ZIP).sha256'
	@echo 'make icon       regenerate the artwork in assets/'
	@echo 'make clean      remove $(BUILD)/ and dist/'
	@echo 'Set APPDIR=... BINDIR=... to install or uninstall somewhere else, BUILD=... to build elsewhere.'
