# Command latency comparison

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="../../../assets/speed-mobile.svg">
    <img src="../../../assets/speed.svg" width="100%" alt="Median milliseconds per action, log scale, Umbra over its socket against Playwright Chromium and WebKit: accessibility snapshot 0.9 against 3.6 and 4.4; click 1.5 against 16 and 66; fill a React field 1.3 against 2.1 and 3.0; eval round trip 0.2 against 0.3 and 0.4; viewport screenshot 50 against 85 and 57; warm navigate 13 against 6.5 and 13, the one action where Chromium is faster.">
  </picture>
</p>

Umbra against Playwright 1.62 on the same local pages of the proving site. Each cell is the median over 20 runs, in milliseconds. Measured on an M1 Pro MacBook Pro with macOS 27; the measurement receipt notes the machine's load during the run. The p95s, the method and every check are in [the full measurement receipt](RESULTS.md).

| | Umbra CLI | Umbra socket | Playwright Chromium | Playwright WebKit |
|---|---:|---:|---:|---:|
| Accessibility snapshot | 14 | **0.9** | 3.6 | 4.4 |
| Click + DOM update | 15 | **1.5** | 16 | 66 |
| Fill a React-controlled field | 15 | **1.3** | 2.1 | 3.0 |
| Eval round trip | 12 | **0.2** | 0.3 | 0.4 |
| Viewport screenshot to a file | 64 | **50** | 85 | 57 |
| Warm navigate (local page) | 27 | 13 | **6.5** | 13 |
| 200-image page | 175 | 163 | **83** | 147 |
| 200-image page, images blocked | 42 | **29** | 76 | 60 |
| Cold start to first page | 408 | | **153** | 718 |
| One-shot `read`, no daemon | 385 | | | |

On these local fixtures, Umbra’s socket path had the lowest median for snapshots, clicks, field fills, eval round trips and screenshots. This is a project-authored command microbenchmark, separate from the external agent evaluation above. Each CLI command adds about 12 ms of process start-up, which is still small next to an agent's thinking time. Chromium starts faster: its figure leaves out Node's own start-up, while Umbra's includes spawning the daemon and WebKit. Chromium also loads a page full of uncached images faster unless you `block images`.
