# Native human browsing verification

2026-11-01. Baseline: `36942f3`. Candidate: the human browsing implementation built with `make build`, identified by its executable hash below.

Visible handoff and sign-in windows now host the existing WKWebView inside native browser controls. They enable history gestures, pinch zoom, keyboard navigation/reload/zoom, progress and load-error feedback, native JavaScript dialog sheets, and the native file chooser. The visible window gets a user-initiated activity token and normal occlusion handling. On displays above 60 Hz, a guarded WebKit feature removes the preference for rendering near 60 fps; hiding the window restores the previous preference, zoom and viewport. No additional page, copied cookies, or replacement web process is needed for handoff.

Sign-in popups now return a real WKWebView using WebKit's supplied configuration, preserving their opener, postMessage and self-close behavior. Handoff popups appear in native windows and hide with their parent. The registry and public command contracts are unchanged.

## Verification

- `make build`: passed; ad hoc bundle signature verified by the build.
- `tests/run.sh`: passed, 427 tests across 22 suites, two skips, plus all smoke checks. [Full log](full-suite.log). One skip is the native GUI test, which runs separately with real windows.
- `UMBRA_TEST_GUI=1 python3 tests/test_daemon.py HumanBrowserTests.test_native_scroll_shortcuts_and_handoff_cleanup -v`: passed. Trusted native wheel input scrolled the page; Command-minus changed the viewport; Command-R and the Reload toolbar button reloaded it; cancelling a confirmation returned false; a file input opened the native chooser and Cancel dismissed it. The window hid and the original viewport returned afterward. [Log](native.log), [native window screenshot](browser.png).
- New headless regressions cover viewport/form preservation and child-window cleanup. The popup authentication regression fails on the baseline with a request deadline and passes on the candidate, preserving window.opener/postMessage and self-close. [Expected baseline failure](popup-before.log).
- Manual Canvas check: Randy entered CWL in a fresh isolated profile using this build. CWL and Duo completed, and Randy reported that scrolling felt smooth. A subsequent read of `/api/v1/users/self/profile` returned HTTP 200 with JSON, at `canvas.ubc.ca/`, without a password field. No identity or response body was saved. [Receipt](canvas.json).

The Canvas blank card did not reproduce in the fresh profile. This does not establish its original root cause or prove that the original profile's stored state has changed. The normal profile was never used for development or testing.

## Frame traces

Environment: M1 Pro MacBook Pro, macOS 27.0.1, internal 120 Hz display. Both arms were app bundles with the same identifier and resources. Each sample used a new isolated profile and a localhost striped page, warmed 30 animation frames, then recorded three seconds of requestAnimationFrame timestamps while scrolling three pixels per frame. Builds alternated order over three repeats. Visible samples began with a native pointer click; focus events were recorded. The extra toolbar changes the visible page height by approximately one CSS pixel. This is a cadence measurement, not physical trackpad-to-display latency or a general website benchmark.

| Build | Mode | Samples | Focus valid | Frames | p50 ms | p95 ms | Max ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | hidden | 3 | n/a | 541 | 17 | 17 | 18 |
| baseline | visible | 3 | 3/3 | 540 | 17 | 18 | 18 |
| candidate | hidden | 3 | n/a | 541 | 17 | 18 | 20 |
| candidate | visible | 3 | 2/3 | 1080 | 8 | 9 | 17 |

All three baseline visible samples and two of the three candidate visible samples retained focus for the measurement. The last candidate sample lost focus, so the strict benchmark command returned exit 1 and the complete foreground comparison is **inconclusive**. Every raw sample is retained in [frames.json](frames.json). The five focus-valid visible samples support the narrower observation of approximately 60 fps baseline versus 120 fps candidate on this fixture; they do not prove that every stall is fixed. No outlier was removed based on its measured speed.

All six post-handoff probes recorded a median 17 ms interval in the same tab after it hid, verifying restoration of the original cadence. The script records 30 raw post-handoff intervals per visible sample.

Earlier measurements are retained too: [initial run](frames-initial.json) includes a 7143 ms pause without verified foreground focus, and [first focus-controlled run](frames-focus-interrupted.json) includes three interrupted visible samples. The initial pause's cause was not isolated. Randy's successful live browsing check is separate from these timing limits.

Reproduce (this deliberately brings native fixture windows to the front):

```sh
python3 bench/human.py --baseline /path/to/baseline/Umbra.app/Contents/MacOS/umbra --output /tmp/frames.json
```

The high-refresh preference is private WebKit SPI, discovered and called only when its feature APIs are available. Unsupported systems keep WebKit's default. Older macOS releases and physical pinch/history gestures were not separately tested.

## Artifact and cleanup

- Baseline executable SHA-256: `24d0a2da52dca8be7b7bab343358615a0e50c9bc78d17f48d4ed9bd77beac22b`
- Candidate executable SHA-256: `903f676b70189ff40b45a20c308779296f6bd5ac34aafdad81f6e6915b28efd3`
- All testing daemons were stopped. The temporary Canvas profile, its WebKit store, socket/log/lock files and download directory were removed. Automated harnesses clean up their own profiles.
- These are pre-delivery test results. Development verification did not modify the installed app or default profile. Subsequent push and installation require the user's explicit authorization under AGENTS.md; consult Git and the installed executable hash for delivery state.
