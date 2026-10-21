# Evaluations and measurements

## MiniWoB++: external agent tasks

All 125 task types, five upstream seed repetitions and both browser arms: **1,250 primary episodes**. Umbra earned full reward on **517/625 (82.7%)**, compared with **489/625 (78.2%)** for Playwright MCP.

The observed difference is inconclusive: its paired 95% task-bootstrap interval is **−0.64 to +9.60 percentage points**. The action budget also treats native key arrays differently; expanding those keys in a conservative post-hoc calculation gives **494/625 vs 489/625**. These are adapted MCP results, not official leaderboard scores.

- [Full results, failures, sensitivity and per-task table](miniwob/2026-10-13/RESULTS.md)
- [Protocol, benchmark selection and reproduction](protocol.md)
- [Verification and retained raw evidence](miniwob/2026-10-13/VERIFICATION.md)
- [Every episode](miniwob/2026-10-13/results.json), [summary](miniwob/2026-10-13/summary.json), [frozen manifest](miniwob/2026-10-13/manifest.json), [raw evidence hashes](miniwob/2026-10-13/evidence-hashes.json)
- [Supplementary serialized clipboard diagnostic](miniwob/2026-10-13/clipboard-serial/RESULTS.md): 10/10 full reward for each arm; primary results remain unchanged.

## Command latency: the README speed figures

[Full comparison and methodology](latency/README.md) · [measurement receipt](latency/RESULTS.md) · [all samples](latency/results.json)

The showcase ratios use the recorded medians, before rounding:

| Same local fixture | Umbra daemon socket | Playwright Chromium | Ratio |
|---|---:|---:|---:|
| Accessibility snapshot | 0.94 ms | 3.57 ms | 3.80× |
| Click + DOM update | 1.52 ms | 16.02 ms | 10.54× |

Twenty measured runs per cell on one M1 Pro Mac. These project-authored fixtures measure direct daemon socket commands, without MCP framing. Playwright clicks include its actionability checks. They do not establish a general website or end-to-end agent speedup. The full table retains metrics where Chromium is faster.

## Memory: the tabs agents leave behind

[Findings, method and limits](memory/README.md) · [every sample](memory/results.json)

On a real Mac, the daemon had run 67 hours with 49 tabs agents had opened and never closed: 17.4 GB and 90% of a CPU core. Tabs now rest when unused (hidden after 5 minutes, closed after 15) and the back-forward cache is off. With one agent still working and ten others gone, memory once they had left fell from **2,756 to 331 MB** (8.3×) and their CPU from **15.3 to 1.9 seconds a minute**. When each of six tabs had visited four pages, memory fell from **3,863 to 312 MB** (12.4×). The pages are synthetic and local; command latency was unchanged.

## Project workflow regression

[Agent setups and coverage](regression/README.md) · [full report](regression/RESULTS.md) · [scores](regression/scores.json) · [fixture and graders](proving-ground.md)

All 70 runs passed: 14 project-authored tasks in five agent setups. This measures the tested workflows, not general browser reliability. Models and Umbra revisions differed between setups.

## Testing and development

[Local test suites, build commands and SVG generation](../development.md) · [security and limitations](../security.md)
