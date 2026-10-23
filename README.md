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

## Go deeper

[Runtime guide](docs/runtime/README.md) · [Recipes](docs/recipes.md) · [Accounts and handoff](docs/sessions.md) · [Architecture](docs/how-it-works.md) · [Security and limitations](docs/security.md) · [Testing and development](docs/development.md) · [FAQ](docs/faq.md)

[MIT](LICENSE) © 2026 Randy Ren
