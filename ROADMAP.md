# Umbra roadmap: the headless browser built for agents

Goal: the best headless browser an AI agent can drive on a Mac. Native system WebKit,
invisible, signed in, fast, and usable through a shell (`umbra …`) or MCP with the same
live state.

## Done when (each item has a check)

| # | Outcome | Check |
|---|---|---|
| 1 | Every command below works through the CLI and through MCP, against the same live tabs | e2e suite, one test per command per transport |
| 2 | Invisible: no visible window (except sign-in or handoff), no Dock icon, never takes focus | Suite asserts every Umbra on-screen window has alpha 0 and the frontmost app is unchanged |
| 3 | Fast: warm snapshot p50 < 25 ms, click < 40 ms, local navigate < 120 ms; cold first command < 700 ms; one-shot read < 500 ms | `bench/` results committed, compared against Playwright when installed |
| 4 | Robust daemon: auto-spawn, idle exit, stale-socket recovery, concurrent clients, one-shots routed through a live daemon | lifecycle tests |
| 5 | Agents succeed: fresh agents given only the skill or MCP tools complete realistic tasks on fixture sites | eval report, at least 90% pass |
| 6 | Existing callers unharmed: FIRST, plus UBC Canvas, Kaltura and ubccheck | their tests and live checks |
| 7 | Shipped: README, docs, llms.txt, skill, CI, pushed | GitHub page reviewed |

## Status at v0.2.0 (2026-10-13)

| # | Result |
|---|---|
| 1 | 41 MCP tools and the same commands on the CLI, sharing live tabs (mcp_conformance 32, test_daemon 102) |
| 2 | Invisibility guard after every session command in the test suite; zero violations. Daemon runs with the prohibited activation policy; focus regression test fails before 16c35ab |
| 3 | All speed targets met at p50 and p95 (bench/results.md): snapshot 0.9 ms socket / 14 ms CLI, click 1.5 / 15 ms, navigate 13 / 27 ms, cold start 408 ms, one-shot read 385 ms |
| 4 | Lifecycle tests: auto-spawn, idle exit, stale socket, racing spawns, concurrent clients, SIGTERM, one-shots through a live daemon |
| 5 | Agent eval 14/14 in each of five arms (Claude Opus and Sonnet in Claude Code, GPT-6 Sol in Codex), zero critical failures (bench/eval/RESULTS.md) |
| 6 | FIRST (110 tests; live Instagram and LinkedIn reads) and UBC (Canvas pulls, downloads, Kaltura, ubccheck) verified on the installed build |
| 7 | README, docs, llms.txt, skill, CI, Homebrew head formula, release zip target |

## Commands (target surface)

- Session: `open` (alias `goto`), `back`, `forward`, `reload`, `tabs`, `tab new|select|close`, `status`, `stop`
- See: `snapshot` (accessibility-style tree with `e<N>` refs; `--interactive`, `--scope`), `text`, `markdown`, `html`, `links`, `screenshot` (viewport, `--full`, element; file or base64), `pdf`
- Act: `click`, `fill`, `type`, `press`, `select`, `check`, `uncheck`, `hover`, `scroll`, `upload`, `dialog`
- Wait: `wait --selector|--text|--url|--js|--idle|--gone`
- Script: `eval` (page world, async)
- Observe: `console`, `network`
- Session data: `fetch`, `download`, `cookies` (names only), `sign-in`, `handoff` (show the tab to the human for 2FA or CAPTCHA, then hide it)
- Shape: `viewport`, `--mobile`, `block images,fonts,media,trackers`
- One-shot (no daemon, kept for scripts): `read`, `fetch`, `download`
- `mcp` (stdio MCP server), `serve` (daemon), `schema` (machine-readable command list)

## Architecture

```
umbra CLI ─┐                       ┌─ Tab "main"  (WKWebView in a transparent,
           ├─ unix socket (JSON) ──┤   off-screen, click-through window)
umbra mcp ─┘   per profile         ├─ Tab …
                                   └─ Store (cookies, keepSession) + profile lock
```

- One daemon per profile owns WebKit, the tabs and the store, and exits after 15 minutes idle (`UMBRA_IDLE`).
- Commands are declared once in a registry (name, params, help, handler). The CLI parser, MCP `tools/list` and `umbra schema` all come from it.
- Page-world scripts run only for `eval` and `read`. Snapshot, refs and actions run in an isolated content world, so pages can't see or tamper with them.
- Input is native and trusted (`isTrusted == true`): NSEvent clicks and keys, plus `insertText` for typing.
- The prototype (2026-10-12) showed a transparent off-screen window with occlusion detection off gives `visibilityState == "visible"` and about 30 animation frames per second. A bare view gives `hidden` and a stalled frame rate. Both deliver trusted input and render screenshots.

## Workstreams

| Lane | Owner | Files | Depends on |
|---|---|---|---|
| F0 foundation: split, engine, daemon, registry, core nav | lead | `Sources/main.swift`, `Store.swift`, `OneShot.swift`, `SignIn.swift`, `Engine.swift`, `Registry.swift`, `Server.swift`, `CoreCommands.swift` | none |
| W1 interaction: snapshot, refs, actions, input, upload, dialogs | agent | `Sources/Interact*.swift` | F0 |
| W2 capture: screenshot, pdf, text/markdown/links/html, console, network, blocking, viewport | agent | `Sources/Capture*.swift` | F0 |
| W3 MCP: protocol, tool schemas, image content, Claude Code setup | agent | `Sources/MCP.swift` | F0 |
| W4 tests, benchmarks, invisibility checks | agent | `tests/`, `bench/` | F0, then W1 to W3 |
| W5 README, docs, llms.txt, skill | agent | `README.md`, `docs/`, `skill/`, `assets/` | W1 to W3 |
| Release: CI, packaging | agent | `Makefile`, `.github/`, `packaging/` | none |

## Ledger

- 2026-10-12: v0.1 (read, fetch, download, sign-in, cookies, profiles, keepSession, `--until`) in production for FIRST and UBC. A download cookie bug was fixed by switching to a top-level navigation that becomes a download.
- 2026-10-13: v0.2.0. Daemon, tabs, snapshot refs, 13 trusted-input actions, capture and observe, MCP server, handoff. Built by parallel lanes (interact, capture, MCP, tests, proving ground, brand, packaging) on the foundation, integrated and verified by the lead. Bugs found by tests and agents and fixed on the way: focus taken at launch, quadratic reply read, SIGPIPE, stop race, scratch tabs and downloads after timeouts, handoff windows left up, attachments shown inline, --profile read too late, form submits not reported, wait --idle ignoring its timeout.
- 2026-10-13: CI green on GitHub's macOS runner after fixes found there (older-compiler type-check limit, cookie listing on a fresh machine, a smoke test assuming port 9 is closed). Open: LifecycleTests.test_five_concurrent_clients (five simultaneous first opens) timed out on the runner in 2 of 6 runs, before and after a load watchdog; never locally (8/8 at load average 67). The test now records whether the daemon still answers and samples its main thread when it happens.
- 2026-10-13: the runner-only timeout, explained. An A/B probe on the runner (8 jobs, test server backlog 5 vs 128) reproduced it in both arms (10 of 48 isolated runs with a backlog of 5, 9 of 48 with 128), so the listen backlog wasn't the cause. The new diagnostics showed it each time: the stuck tab's request never reached the server, and a script in that tab got no answer, so its web process was wedged. The load watchdog asked that process for `document.readyState` and stopped when no answer came. Now two silent checks end the process and retry the load in a new one (`test_a_load_whose_web_process_stops_answering_moves_to_a_new_one` hangs for 60 s before the fix, passes in about 20 s after).
- 2026-10-13: the sign-in window became a way to sign in, never a way to browse (opening Umbra showed signed-in Instagram and LinkedIn, which are distracting). The daemon shows it, with a Dock icon while it is open; signed-in sites are checked without loading, and a login page closes the moment its sign-in lands.
- 2026-10-13: after the fix the five-concurrent test passed 96 of 96 isolated runs on the runner (19 of 96 failed before). The account's GitHub Actions minutes then ran out (macOS jobs count ten times on a private repo, and the two probes used most of them), so both workflows are disabled (`gh workflow enable CI` turns CI back on). Until then, tests run locally: `make test`, about five minutes, never touching the default profile.
- 2026-10-13: Add Account. A pasted login link becomes a site in sites.json; during its first sign-in Umbra learns the cookie it set, so the site is then checked like the others and agents can use it. `umbra sign-in <url> --save NAME` does the same from a shell.
- 2026-10-13: the agent eval with Codex (`eval.py run-codex`, GPT-6 Sol at high effort): 14/14 with the skill in the prompt and 14/14 as installed, zero critical failures by `process.py`; in every as-installed run Codex read its installed skill unprompted. README diagrams for the loop, WebKit, sign-in, handoff, profiles, the eval and speed, each with a phone layout; `assets/charts.py` draws the data ones from bench/results.json and bench/eval/scores.json.
