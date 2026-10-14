# Verification receipt

The primary campaign ran all 125 upstream task types with the five seeds from the pinned BrowserGym configuration in each of two arms: 1,250 episodes. Both arms used fresh Codex sessions with the same model and prompts. The browser protocol was adapted to filtered MCP tools.

Checks completed against the actual artifacts:

- All 1,250 run receipts are present; all 625 paired setups have identical goal text.
- The Umbra executable, frozen harness and five pinned BrowserGym source hashes match the manifest.
- All controller JSON and tool-call JSONL files parse successfully.
- No completed candidate trace used command execution, changed files or called another MCP server. Missing, malformed and started forbidden actions are rejected by the reporter.
- All 1,250 cleanup receipts report success. A separate census found zero remaining main-campaign profile stores, support files, downloads or browser/broker process roots for the checked run paths.
- Six real-browser controls validated unfinished, correct and wrong actions against the original grader in both engines. Four agent pilot runs earned full reward. These simple controls are calibration evidence, not broad reliability evidence.
- Reporter controls accept a valid completed result and reject missing completion, missing/empty/malformed traces and forbidden actions that started without completing. Failure classification correctly handles a rejected tenth action and observation exhaustion.

Independent review checked the task/seed pairing, source hashes, grading, grouping of bootstrap samples, calibration and report claims. It found reporting defects that were corrected before final reporting. No frozen candidate/grader code was changed during the main campaign.

One Playwright case has no outcome evidence: `miniwob.multi-orderings`, repetition 3. The candidate stated that browser tools were unavailable and made no tool calls. Setup had produced a ready receipt; the exact cause is unresolved. It remains a failure in the denominator.

The audit identified two material comparison caveats. Native press arrays counted as one interaction unit, so 23 positive-reward Umbra outcomes exceed ten units when the keys are expanded individually. The report gives a conservative post-hoc sensitivity calculation and retains the original score. Native clipboard actions also share macOS state across profiles; their serial diagnostic is reported separately.

The primary full-reward difference is 4.48 percentage points, with a task-grouped 95% bootstrap interval from -0.64 to 9.604 points. This does not establish superiority. Neither the full corpus score nor these controls establish real-site reliability or pure browser-engine speed.

The separate serialized clipboard diagnostic completed all 20 episodes, with full reward on 10/10 for each arm. It reused the original five seeds and paired goal text, with one episode at a time. Its ten native profiles, all Chromium profile directories and scoped process roots were absent in the final census. Model strategy and machine load varied; this does not prove clipboard contention caused the primary failures.

## Retained evidence

Raw transcripts, tool calls, grading receipts, screenshots, calibration, frozen harness and reporter snapshots are retained in the ignored local archive `bench/external/evidence/2026-10-13.tar.gz`, alongside the tested binary and upstream source snapshots. `archive.json` gives the archive SHA-256 and inventory. The published per-file hash lists identify the campaign files inside it. The archive excludes `.git`, browser profile directories and transient dependency installations.
