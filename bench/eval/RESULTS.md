# Agent eval: can a fresh agent get real work done with Umbra?

Run 2026-10-13 on a MacBook Pro (M1 Pro, macOS 27.0.1) with Umbra installed from 0eecdd3.
Harness: `bench/eval/eval.py`. Tasks and graders: `bench/tasks.json`, `bench/grade.py`.

## Claim

A fresh agent given the Umbra skill (or only `umbra help`) can complete realistic browser
tasks on a real-looking web app, with zero critical failures. A critical failure is driving
Safari or AppleScript, escaping its throwaway profile, reading the repo's answer sources, or
using Computer Use.

## Setup

- **Tasks:** 14 requests from the proving ground (Northwind Ops), worded as a user would ask:
  - sign in with a password or an OTP
  - read a paginated table
  - total a filtered list
  - refund behind a `confirm()`
  - export a CSV download
  - upload through a hidden file input
  - a 3-step validated wizard
  - infinite scroll
  - a React-controlled input
  - a contenteditable field
  - a shadow-DOM picker
  - a same-origin iframe form
  - a `target=_blank` report
  - content that loads late

  The 15th task, `captcha-handoff`, needs a person at the screen and wasn't run.
- **Isolation:** each candidate is a fresh Claude Code subagent with no conversation history. It gets its own site server (seed 7), its own workdir, and an `umbra` wrapper pinned to its own profile. It never sees the graders or expected values.
- **Grading:** objective. `grade.py` reads the server's event log and state, the answer text, and the output files.
- **Process checks:** read from each candidate's full tool transcript.

| Arm | Model | Guidance |
|---|---|---|
| opus-skill | Claude Opus 5.5 | `skill/SKILL.md` in the prompt |
| opus-bare | Claude Opus 5.5 | only "the browser is Umbra; `umbra help` explains it" |
| sonnet-skill | Claude Sonnet 5.5 | `skill/SKILL.md` in the prompt |

## Results

| Task | opus-skill | opus-bare | sonnet-skill |
|---|:-:|:-:|:-:|
| pending-count | PASS | PASS | PASS |
| shipped-total | PASS | PASS | PASS |
| refund-1017 | PASS | PASS | PASS |
| export-csv | PASS | PASS | PASS |
| upload-invoice | PASS | PASS | PASS |
| wizard | PASS | PASS | PASS |
| feed-featured | PASS | PASS | PASS |
| display-name | PASS | PASS | PASS |
| bio | PASS | PASS | PASS |
| theme-teal | PASS | PASS | PASS |
| iframe-notifications | PASS | PASS | PASS |
| popup-report | PASS | PASS | PASS |
| otp-login | PASS | PASS | PASS |
| slow-forecast | PASS | PASS | PASS |
| **Total** | **14/14** | **14/14** | **14/14** |

| Process (per arm, 14 runs) | opus-skill | opus-bare | sonnet-skill |
|---|:-:|:-:|:-:|
| Critical failures | 0 | 0 | 0 |
| Median tool calls per task | 8 | 9 | 6.5 |
| Total tool calls | 116 | 128 | 89 |
| `umbra help` lookups (all runs) | 2 | 21 | 1 |
| Runs that used `sleep` instead of `wait` | 0 | 2 | 2 |
| Median wall time per task | 28 s | 29.5 s | 21.5 s |

## What the eval changed

1. **The skill's safety rules were too blunt.** The first version said "get the user's explicit OK before purchases, posts, submissions, refunds…". The opus-skill refund candidate then stopped and asked for permission, even though the request said "Please refund it", and failed (13/14). The rules are now conditions on the request: do what was asked, ask before consequential actions that are your own idea, and use credentials the request provides. Re-run, the refund passed every time.
2. **`wait --idle` ignored `--timeout`.** An opus-bare candidate's `wait --idle` ran past two minutes and had to be killed. It's fixed in 2dd2701: it now fails at the timeout with a hint to wait for text or a selector.
3. **The harness itself had a bug.** One batch gave three reps of a task the same profile. The agents noticed tabs and sign-ins changing under them and moved to scratch profiles on their own, but the batch was discarded and re-run with a profile per run. The lesson went into the docs: parallel agents need separate profiles (or `--tab`), and cookies on 127.0.0.1 are shared across ports.

## Reading the results

- Umbra is usable without the skill: opus-bare passed everything from `umbra help` alone, so the CLI's help text holds up for agents. The skill's value is efficiency and discipline: about 10× fewer help lookups, and none of opus-skill's runs fell back to `sleep`.
- A smaller model with the skill passed everything, with the fewest tool calls.
- Limits:
  - Each arm ran each task once (n = 1 per cell).
  - The tasks were written by this project's own proving lane, not held out from it.
  - Everything ran on one machine, alongside other work.
  - CAPTCHA/handoff behaviour isn't covered.
  - Only Claude models were tested.

Decision: promote the revised skill.
