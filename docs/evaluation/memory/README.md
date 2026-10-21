# Memory: the tabs agents leave behind

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="../../../assets/memory-mobile.svg">
    <img src="../../../assets/memory.svg" width="100%" alt="Memory of Umbra and its WebKit processes while one agent keeps working and ten others have each left a heavy page open. Before, the abandoned tabs stayed open at about 2.7 GB. Now each tab closes once unused for the idle limit, and memory falls to 331 MB: 8.3 times less. CPU while the tabs sit unused fell from 15.3 to 1.9 seconds a minute, 8.2 times less. When each tab visited four pages, memory fell from 3,863 to 312 MB, 12.4 times less, and the peak from 4,271 to 1,825 MB.">
  </picture>
</p>

## What we found

An audit of a real Mac on 2026-10-21 found the default profile's daemon running for 67 hours with 49 tabs open. Agents had opened every one of them by name for a task and never closed it. Together they held 17.4 GB across 47 WebKit processes and kept 90% of a CPU core busy. A tab closed only when its daemon exited, after 15 minutes without a command, and a Mac where agents work all day never gives it those 15 minutes. Every tab also kept running its animations and timers, because Umbra's pages always see themselves as visible.

## What changed

- **A tab rests when no command uses it.** After a third of `UMBRA_IDLE` (5 minutes by default) Umbra hides it, so its timers slow and its animations pause, as a background tab does in Safari. The next command on it shows it again first. After all of `UMBRA_IDLE` (15 minutes) it closes, and its web process ends. [How it works](../../how-it-works.md#the-daemon).
- **No back-forward cache.** WebKit kept each page a tab left frozen in memory, often in a process of its own, in case the tab went back. Going back now loads the page again, from the HTTP cache where it can.
- **Agents are asked to close their tabs** (`tab close NAME`) in the skill, the MCP instructions and `llms.txt`.

## The measurement

`bench/memory.py` starts a daemon under a throwaway profile. One agent opens a tab and takes a snapshot every 10 seconds for the whole run, which keeps the daemon busy as on a real Mac. Each of the other agents opens a heavy page in a tab of its own, takes a snapshot, clicks and reads the text, and never comes back. The heavy page holds memory the way a web app does: a JavaScript heap, a typed array, a drawn canvas, decoded images and a few thousand DOM nodes, kept busy by an animation and a timer. Every 5 seconds the bench adds up the physical footprint (Activity Monitor's Memory column) and CPU time of the daemon and of every process macOS holds it responsible for: its web content, GPU and networking processes. `UMBRA_IDLE` is 60 seconds here so a run takes minutes rather than an hour.

| Workload | | Peak | After they leave | Web processes then | CPU while the tabs sit unused |
|---|---|---:|---:|---:|---:|
| 10 agents, 1 page each | Before | 2,774 MB | 2,756 MB | 11 | 15.3 s / min |
| | Now | 2,786 MB | **331 MB** | **1** | **1.9 s / min** |
| 6 agents, 4 pages each (two sites, alternating) | Before | 4,271 MB | 3,863 MB | 19 | 10.3 s / min |
| | Now | **1,825 MB** | **312 MB** | **1** | **2.0 s / min** |

"After they leave" is the last sample, 145 seconds after the last agent left its tab. "Before" is commit 2ff860c. Hiding alone gave back 4 to 9% of the abandoned tabs' memory and almost all of their CPU; closing them gave back the memory. On its own, turning off the back-forward cache took 4 tabs that had each visited 6 pages (plus the working tab) from 3.4 GB to 1.3 GB.

Command latency did not change. `bench/bench.py`, 20 runs per command on both builds, Umbra CLI medians before and now: snapshot 15.8 and 14.9 ms, click 16.4 and 16.2 ms, fill 16.2 and 15.9 ms, warm navigate 22.7 and 22.4 ms, screenshot 66.9 and 66.2 ms, cold start 453 and 448 ms.

Every sample, both builds and the latency medians are in [results.json](results.json). Measured on an M1 Pro MacBook Pro with 16 GB and macOS 27.0.1.

## Limits

- The pages are synthetic and local, so a real site's numbers differ. A real site's caches (images, compiled scripts, layers) are larger, so hiding may give back more than it did here; its live JavaScript data is what only closing gives back.
- A tab an agent leaves still holds its memory until it closes: 15 minutes by default.
- Measured but not adopted: one shared WebKit process pool for all tabs. It saves one spare web process (about 7 MB) per tab, through an API Apple has deprecated.

## Run it

```sh
make build
python3 bench/memory.py                                  # 10 agents, 1 page each
python3 bench/memory.py --abandoned 6 --pages 4          # 6 agents, 4 pages each
python3 bench/memory.py --umbra OTHER/Umbra.app/Contents/MacOS/umbra --json run.json
python3 assets/charts.py                                 # redraw the picture from results.json
```
