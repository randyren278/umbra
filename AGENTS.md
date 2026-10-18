# Working on Umbra (for agents and people)

Umbra is a headless browser built for agents: native WebKit on macOS, invisible, signed in.
Read ROADMAP.md for the goal and the acceptance checks.

## Safety rules (never break these)

1. **Never touch the default profile.** It is the owner's real, signed-in cookie store, and scheduled jobs use it every minute. Every command you run while developing or testing sets `UMBRA_PROFILE=<lane>-<random>`. Afterward delete that profile's store and files:
   - `~/Library/WebKit/com.randyren.umbra/WebsiteDataStore/<uuid>`, where `<uuid>` is SHA-256 of `"umbra profile " + name`: uppercase hex, first 32 characters split 8-4-4-4-12, then lowercased
   - `~/Library/Application Support/Umbra/*-<name>.*` (lock, socket, log)
   - `~/Downloads/Umbra/<name>/`
   - a keep-signed-in launch agent, if the profile marked a site: `launchctl bootout gui/$(id -u)/com.randyren.umbra.keep-signed-in.<name>`, and its plist (set `UMBRA_AGENTS` to a temporary folder so it never lands in `~/Library/LaunchAgents`)
2. **Never run `make install`**, and never touch `~/Applications/Umbra.app` or `~/.local/bin/umbra`; live jobs run them. Build with `make build` and run `build/Umbra.app/Contents/MacOS/umbra`.
3. **Never use Safari, AppleScript or osascript.** Tests visit only 127.0.0.1 servers. A real public site is allowed only for a manual check (example.com).
4. **Stop the daemons you start**: `UMBRA_PROFILE=… umbra stop`. An idle daemon exits after `UMBRA_IDLE` seconds (default 900); tests should set `UMBRA_IDLE=60`.
5. Stay inside the files your lane owns (ROADMAP.md, Workstreams). Don't git commit or push; the lead integrates.
6. Never print, log or return cookie values.

## Architecture in one screen

- `main.swift`: the CLI. Daemon commands are parsed from the registry and sent to the daemon with `call()`. `read`, `fetch` and `download` keep their one-shot contract (OneShot.swift) and go through the daemon when one is running.
- `Server.swift`: one daemon per profile on a unix socket. A request is one JSON object per line, `{id, command, args}`. A reply is `{id, ok, result}` or `{id, ok: false, error: {code, message}}`. Each connection gets a thread, and handlers run on the main thread.
- `Registry.swift`: `Command(name, help, params, aliases, usesTab, mcp, timeout, run)`. `run(args, engine, reply)` must call `reply` exactly once. The CLI parser, `umbra help`, `umbra schema` and the MCP tool list are all generated from it, so a new command needs nothing else.
- `Engine.swift`: an `Engine` holds `Tab`s. Each tab is a `WKWebView` in a borderless, transparent, click-through window placed at (-30000, -30000). WebKit's occlusion detection is turned off, so the page reports `visibilityState == "visible"` and runs `requestAnimationFrame`. The tab handles navigation (`load`, `go`, `loading` waiters, `status`), pop-ups (they become tabs), dialogs (`dialogs` log, `nextDialog`), the file chooser (`uploads`), page downloads (`downloads`, saved in ~/Downloads/Umbra), handoff (`reveal`, `endHandoff`), scripts (`run(body, args, in: world)`, `wait(until:)`) and per-lane state (`extras`). `tabSetups` hooks run for every new tab before its first load: install user scripts, message handlers and content rules there.
- `umbraWorld`: an isolated JavaScript world (shared DOM, separate globals). Put snapshot, ref and action code there so pages can't see or break it. Use `.page` only for user `eval` and for instrumenting page APIs (console, fetch).
- Native input: synthesized `NSEvent`s sent straight to the web view arrive in the page with `isTrusted == true`. Window coordinates are the CSS viewport coordinates with y flipped: `y = viewHeight - cssY`.
- Results: return a JSON-ready dictionary with a short human `text` (what the CLI prints) plus structured fields. Binary data goes in `data` (base64) with a `mime` field, or `path` when the caller passed one.
- Exit codes: 0 ok, 1 Umbra failed, 2 timeout or script error, 3 signed out, 4 page or download failed, 5 HTTP error, 64 usage, 78 bad sites file.

## Build and test

```sh
make build                      # build/Umbra.app
tests/run.sh                    # the whole suite (builds if stale); always uses throwaway profiles
UMBRA_PROFILE=dev-$RANDOM build/Umbra.app/Contents/MacOS/umbra open example.com
```

Keep code like the existing code: short names, few comments, no speculative options. A test
that fails is a bug to fix, not a test to loosen.
