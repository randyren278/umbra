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

## Project workflow regression

[Agent setups and coverage](regression/README.md) · [full report](regression/RESULTS.md) · [scores](regression/scores.json) · [fixture and graders](proving-ground.md)

All 70 runs passed: 14 project-authored tasks in five agent setups. This measures the tested workflows, not general browser reliability. Models and Umbra revisions differed between setups.

## Testing and development

[Local test suites, build commands and SVG generation](../development.md) · [security and limitations](../security.md)
