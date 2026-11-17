<p align="center">
  <img src="assets/banner.svg" width="100%" alt="Umbra: the headless browser built for agents. Native WebKit, invisible, signed in.">
</p>

<p align="center">
  <strong>Sign in once. Let your agents do the browsing.</strong><br>
  Umbra gives your agent a fast, persistent browser on your Mac. It uses native WebKit, keeps your accounts signed in, and works invisibly while your screen and focus stay yours.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#benchmarks">Benchmarks</a> ·
  <a href="docs/mcp.md">MCP setup</a> ·
  <a href="docs/README.md">Documentation</a>
</p>

## Benchmarks

<!-- runtime-panel: competitive-benchmarks -->
<picture>
  <source media="(max-width: 600px)" srcset="assets/docs/competitive-benchmarks-mobile-3184cba02d.svg">
  <img src="assets/docs/competitive-benchmarks-de969cdbf6.svg" width="100%" alt="CLICKS + DOM UPDATE: Umbra 1.52 ms; Playwright Chromium 16.02 ms (10.5×); Playwright WebKit 65.85 ms (43.3×); ACCESSIBILITY SNAPSHOTS: Umbra 0.94 ms; Playwright Chromium 3.57 ms (3.8×); Playwright WebKit 4.41 ms (4.7×); CONTROLLED FORM FILLING: Umbra 1.27 ms; Playwright Chromium 2.13 ms (1.7×); Playwright WebKit 2.96 ms (2.3×); PNG SCREENSHOTS: Umbra 50.16 ms; Playwright Chromium 84.86 ms (1.7×); Playwright WebKit 57.12 ms (1.1×). Same local fixtures · 20-run medians · Apple M1 Pro · recorded 2026-10-13. Umbra daemon socket vs Playwright 1.62.1 drivers. Excludes fresh CLI/MCP overhead. Clicks include each tool’s actionability policy. STATEFUL MCP OBSERVATIONS: 50% fewer canonical result tokens vs Playwright MCP Chromium and WebKit; Umbra 12,491, Chromium 25,434, WebKit 25,211. Complete task prose, form state and actionable controls retained. 27 local states × 3 repeats per browser · median totals · o200k_base · 2026-10-23 UTC. Umbra opt-in observe deltas + full text on every state vs Playwright MCP 0.0.83 full snapshots. Initial/reset snapshots included. Canonical result JSON only; setup/actions excluded. Not total agent or billed tokens.">
</picture>
<!-- /runtime-panel -->

[Speed benchmarks](docs/evaluation/latency/README.md) · [Token benchmark, method and raw results](docs/evaluation/observation-tokens/README.md)

<!-- runtime-panel: low-memory-comparison -->
<picture>
  <source media="(max-width: 600px)" srcset="assets/docs/low-memory-comparison-mobile-6de8fd3083.svg">
  <img src="assets/docs/low-memory-comparison-9b35b27a08.svg" width="100%" alt="Matched local signed-in-like fixture, three reordered sessions per mode on M1 Pro. After ten task mixes, stock four-WebKit footprint was 371–379 MiB versus 75–76 MiB with three API views and one retained WebKit view. After ten native actions, stock was 438–443 MiB versus 221–222 MiB hybrid, a paired 49.3–50.1 percent reduction. All views retained. Native physical footprint includes client helpers, not summed RSS or globally deduplicated RAM. No live-site benchmark.">
</picture>
<!-- /runtime-panel -->

The hybrid keeps three supported API views and one live WebKit fallback. Measurements use the same signed-in-like local tasks on an M1 Pro, with new isolated profiles and all views retained. Physical footprint includes daemon and WebKit helpers; it is not summed RSS or globally deduplicated RAM. [Results, methodology and reproduction](docs/evaluation/low-memory/README.md).


## Why Umbra

<!-- runtime-panel: why-umbra -->
<picture>
  <source media="(max-width: 600px)" srcset="assets/docs/why-umbra-mobile-b3e946ebe0.svg">
  <img src="assets/docs/why-umbra-f523332d30.svg" width="100%" alt="Native to your Mac: WebKit is already there. A small Swift app gives your agent direct browser commands. Sign in once. Keep going: Persistent accounts in Umbra’s own store. Your agents pick up the session across commands. Your screen stays yours: Browse invisibly in the background. Bring a page forward for sign-in or a human handoff. One session. Every tool: Shell, MCP, Python, TypeScript and Swift share live tabs. Bring Codex, Claude Code or Cursor. Agents that work together: Named tabs, ownership leases, bounded admission, deadlines and cancellation coordinate parallel work. Read less. Do more: Observe changes, extract typed records, batch checked steps and resume explicit checkpoints.">
</picture>
<!-- /runtime-panel -->

[Shared runtime](docs/runtime/README.md) · [Persistent clients](sdk/README.md) · [Accounts and handoff](docs/sessions.md)

## One continuous browsing session

<picture>
  <source media="(max-width: 600px)" srcset="assets/the-loop-mobile.svg">
  <img src="assets/the-loop.svg" width="100%" alt="Open a page, read its snapshot, act on an element ref, then read the updated page. Umbra keeps the same signed-in tab throughout.">
</picture>

Tabs stay alive between calls. Read the page as a compact outline, click or fill an element, and keep going. [See a complete workflow](docs/walkthrough.md).

## Install

**macOS 14 or later**, with the Xcode Command Line Tools (`xcode-select --install`).

```sh
git clone https://github.com/randyren278/umbra.git
cd umbra
make install
```

Installs `~/Applications/Umbra.app` and links `~/.local/bin/umbra`. [Installation and agent skill setup](docs/install.md).

## Start browsing

```sh
umbra open example.com
umbra snapshot --interactive
umbra markdown --main
```

For Claude Code, Codex, Claude Desktop or Cursor:

```sh
umbra mcp --print-config
```

[Connect over MCP](docs/mcp.md) · [Teach your agent](docs/agents.md) · [Browse the commands](docs/commands.md)

## Opt-in low-memory sessions

Use [declared API sessions](docs/api.md) for supported same-origin JSON routes without creating a browser. For browser tasks that can run without visibility-dependent rendering, opt a new tab into nonvisual mode:

```sh
umbra open example.com --new --tab reading --nonvisual
umbra dom read --tab reading
umbra dom find --tab reading --query urgent
umbra render --tab reading
```

The tab retains its WebKit view and JavaScript state. Native input and capture promote it automatically; `render` promotes explicitly without reloading. Promotion is sticky, and the offscreen window remains invisible. Animation frames may pause before promotion. Ordinary browser commands retain their existing defaults. [Modes, API contracts and limitations](docs/api.md).

<!-- runtime-panel: low-memory-reads -->
<picture>
  <source media="(max-width: 600px)" srcset="assets/docs/low-memory-reads-mobile-eda1b6e08f.svg">
  <img src="assets/docs/low-memory-reads-d7bfb13bf1.svg" width="100%" alt="Separate matched read cohort with three sessions per mode. Candidate footprint: 20.1–20.2 MiB for three API views without WebKit; 68.2–71.4 MiB with a retained windowless JavaScript WebKit view; 225.2–228.0 MiB after native work. Across three candidate sessions, 90 API reads and 90 DOM read/find/link tasks passed without render-host promotion. Native and pixel work promoted once per session, preserving the same view and matching pixels. C3 equivalent reads also stayed windowless; no additional RAM reduction demonstrated. Not live-site measurements.">
</picture>
<!-- /runtime-panel -->

`api read` uses declared GET/HEAD routes. `dom read/find/links` reads bounded main-document structure without style, geometry or input; CSS-hidden text may be included. Native input, geometry and pixels need the full browser. These explicit contracts expand no-render task coverage; they do not demonstrate a further RAM reduction over efficient C3 reads. [Read scope and fallback](docs/semantic.md) · [Benchmark limits](docs/evaluation/low-memory/README.md).

## Go deeper

[Explicit no-render reads](docs/semantic.md) · [Runtime guide](docs/runtime/README.md) · [Recipes](docs/recipes.md) · [Accounts and handoff](docs/sessions.md) · [Architecture](docs/how-it-works.md) · [Security and limitations](docs/security.md) · [Testing and development](docs/development.md) · [FAQ](docs/faq.md)

[MIT](LICENSE) © 2026 Randy Ren
