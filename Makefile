# Builds Umbra.app, installs it to ~/Applications and links `umbra` into ~/.local/bin.
APP := build/Umbra.app
BIN := $(APP)/Contents/MacOS/umbra
INSTALLED := $(HOME)/Applications/Umbra.app
LINK := $(HOME)/.local/bin/umbra

.PHONY: build install test clean

build: $(BIN) $(APP)/Contents/Info.plist $(APP)/Contents/Resources/AppIcon.icns

# The bundle identifier is embedded in the binary as well, so the cookie store is the same
# however umbra is launched (from the app, a symlink or a copy).
$(BIN): Sources/umbra.swift Info.plist
	mkdir -p $(@D)
	xcrun swiftc -O -swift-version 5 -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist \
		-o $@.tmp Sources/umbra.swift
	mv $@.tmp $@

$(APP)/Contents/Info.plist: Info.plist
	mkdir -p $(@D)
	cp $< $@

$(APP)/Contents/Resources/AppIcon.icns: assets/AppIcon.icns
	mkdir -p $(@D)
	cp $< $@

# Copies beside the installed app, then swaps it in by rename so a running job never sees half an app.
install: build
	mkdir -p $(HOME)/Applications $(dir $(LINK))
	rm -rf $(INSTALLED).new $(INSTALLED).old
	ditto $(APP) $(INSTALLED).new
	if [ -d $(INSTALLED) ]; then mv $(INSTALLED) $(INSTALLED).old; fi
	mv $(INSTALLED).new $(INSTALLED)
	rm -rf $(INSTALLED).old
	ln -sfn $(INSTALLED)/Contents/MacOS/umbra $(LINK)

test: install
	tests/smoke.sh

clean:
	rm -rf build
