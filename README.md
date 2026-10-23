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

**1.32–1.47× more tasks per second. About 82–84% less sampled peak memory.** Three matched single-worker runs of the same four-task localhost workload, with all 80 measured tasks per arm succeeding in each repeat. Memory includes the daemon and its responsible WebKit/GPU/network processes. [October 23 measurements and every raw row](docs/evaluation/runtime/primary-v2/README.md).

**Over 50% fewer observation tokens through CLI, MCP and Python.** The final build reconstructed text, refs and metadata exactly throughout a 16-state journey, including navigation resets and unchanged reads.

| Observation transport | Full tokens | Incremental tokens | Reduction |
|---|---:|---:|---:|
| CLI JSON | 5,911 | 2,865 | 51.5% |
| MCP | 5,660 | 2,631 | 53.5% |
| Python SDK | 10,280 | 4,328 | 57.9% |

Counts cover delivered observation responses with tiktoken 0.12.0 / o200k_base; initial and navigation-reset full responses stay counted. **1, 4, 8, 16 and 32 concurrent clients passed ownership, progress and cancellation checks.** [Final-build verification and methods](docs/evaluation/runtime/RESULTS.md).

Those figures describe the single-worker fixture. Heavy matched-WebKit runs at 32 workers exceeded the 2 GiB budget, so the full-grid throughput, RAM and latency targets remain unmet.

**0.94 ms snapshots. 1.52 ms clicks with DOM updates.** Earlier daemon-socket medians recorded October 13, 2026 on local fixtures, 20 runs per cell. In the October 21 abandoned-tab fixture, whole-browser memory fell from **2,756 to 331 MB** after idle tabs closed. [Methods, baselines and all samples](docs/evaluation/README.md).

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

The latency figures compare the same local fixture with Playwright Chromium and exclude MCP framing; Chromium navigated faster. Memory figures measure synthetic pages after abandoned tabs close, with a 60-second test idle limit. These results describe the recorded workloads, not a general website speedup.

[Explore the complete evaluations, comparisons, methods and data →](docs/evaluation/README.md)

## Why Umbra

- **Native WebKit. No browser download.** The engine is already on your Mac. Umbra is a small Swift app that gives your agent direct browser commands.
- **Signed in and ready.** Log in through Umbra's own window. Agents keep using the session across commands, in a store separate from Safari.
- **Invisible until you need it.** Background tabs leave your desktop alone. Sign-in and handoff bring the page to you for the human steps.
- **Shell and MCP, together.** Your scripts and agents share live tabs, history and accounts. Use Claude Code, Codex, Cursor or your own tooling.
- **Parallel work with explicit ownership.** Named tabs, agent/task leases, bounded admission, deadlines and cancellation help cooperating agents share a profile. [Runtime guide](docs/runtime/README.md).
- **Smaller observations and reusable steps.** Read line deltas, extract typed DOM records, run checked batches and resume explicit workflow checkpoints. [Persistent clients](sdk/README.md) support Python, TypeScript and Swift.

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
