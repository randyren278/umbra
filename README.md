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

<a href="docs/evaluation/README.md">
  <picture>
    <source media="(max-width: 600px)" srcset="assets/showcase-mobile-v1.svg">
    <img src="assets/showcase-v1.svg" width="100%" alt="Measured local-fixture medians: Umbra daemon socket snapshots 0.94 ms versus Playwright Chromium 3.57 ms, 3.8× faster; clicks plus DOM update 1.52 ms versus 16.02 ms, 10.5× faster. Umbra earned full reward on 517/625 MiniWoB++ cases (82.7%) with an adapted MCP protocol, and passed 70/70 project-authored workflow regression runs.">
  </picture>
</a>

<a href="docs/evaluation/memory/README.md">
  <picture>
    <source media="(max-width: 600px)" srcset="assets/memory-mobile.svg">
    <img src="assets/memory.svg" width="100%" alt="Memory of Umbra and its WebKit processes while one agent keeps working and ten others have each left a heavy page open. Before, the abandoned tabs stayed open at about 2.7 GB. Now each tab closes once unused and memory falls to 331 MB, 8.3 times less; their CPU fell from 15.3 to 1.9 seconds a minute. With four pages per tab, memory fell from 3,863 to 312 MB, 12.4 times less.">
  </picture>
</a>

[Explore the complete evaluations, comparisons, methods and data →](docs/evaluation/README.md)

## Why Umbra

- **Native WebKit. No browser download.** The engine is already on your Mac. Umbra is a small Swift app that gives your agent direct browser commands.
- **Signed in and ready.** Log in through Umbra's own window. Agents keep using the session across commands, in a store separate from Safari.
- **Invisible until you need it.** Background tabs leave your desktop alone. Sign-in and handoff bring the page to you for the human steps.
- **Shell and MCP, together.** Your scripts and agents share live tabs, history and accounts. Use Claude Code, Codex, Cursor or your own tooling.

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

[Recipes](docs/recipes.md) · [Accounts and handoff](docs/sessions.md) · [Architecture](docs/how-it-works.md) · [Security and limitations](docs/security.md) · [Testing and development](docs/development.md) · [FAQ](docs/faq.md)

[MIT](LICENSE) © 2026 Randy Ren
