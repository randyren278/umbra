# Agent eval: can a fresh agent get real work done with Umbra?

Run 2026-10-13 on a MacBook Pro (M1 Pro, macOS 27.0.1): the Claude arms with Umbra installed from 0eecdd3, the Codex arms with it installed from 30c0835.
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
- **Isolation:** each candidate is a fresh Claude Code subagent with no conversation history, or a fresh `codex exec --ephemeral` run (`eval.py run-codex`). It gets its own site server (seed 7), its own workdir, and an `umbra` wrapper pinned to its own profile. It never sees the graders or expected values.
- **Grading:** objective. `grade.py` reads the server's event log and state, the answer text, and the output files.
- **Process checks:** read from each candidate's full tool transcript; for Codex, by `bench/eval/process.py`, which flags every command or tool call that touches Safari or AppleScript, leaves the run's profile, reads this repo (other than the skill), or uses another browser or screen tool.
- **Claude's setup:** each candidate is a Claude Code subagent on this Mac, where the skill was installed (`~/.claude/skills/umbra`) and the global `~/.claude/CLAUDE.md` names it. So in opus-bare, "only `umbra help`" means nothing about the skill in the task prompt, not that the skill was out of reach.
- **Codex's setup:** the user's own Codex install (model, `~/.codex/skills/umbra`, global `AGENTS.md`), with three changes for isolation: the Umbra MCP server pinned to the run's profile (`-c mcp_servers.umbra.env.UMBRA_PROFILE=…`), the `node_repl` browser tool off (it would take over the screen), and `HERA_OFF=1` with no turn notifications so the runs leave no trace in the user's notes.

| Arm | Model | Guidance |
|---|---|---|
| opus-skill | Claude Opus 5.5 | `skill/SKILL.md` in the prompt |
| opus-bare | Claude Opus 5.5 | only "the browser is Umbra; `umbra help` explains it" |
| sonnet-skill | Claude Sonnet 5.5 | `skill/SKILL.md` in the prompt |
| codex-skill | GPT-6 Sol in Codex (codex-cli 0.162.1, model gpt-6-sol, reasoning effort high) | `skill/SKILL.md` in the prompt |
| codex-bare | the same | only "the browser is Umbra; `umbra help` explains it", with Codex's installed skill available as usual |

## Results

| Task | opus-skill | opus-bare | sonnet-skill | codex-skill | codex-bare |
|---|:-:|:-:|:-:|:-:|:-:|
| pending-count | PASS | PASS | PASS | PASS | PASS |
| shipped-total | PASS | PASS | PASS | PASS | PASS |
| refund-1017 | PASS | PASS | PASS | PASS | PASS |
| export-csv | PASS | PASS | PASS | PASS | PASS |
| upload-invoice | PASS | PASS | PASS | PASS | PASS |
| wizard | PASS | PASS | PASS | PASS | PASS |
| feed-featured | PASS | PASS | PASS | PASS | PASS |
| display-name | PASS | PASS | PASS | PASS | PASS |
| bio | PASS | PASS | PASS | PASS | PASS |
| theme-teal | PASS | PASS | PASS | PASS | PASS |
| iframe-notifications | PASS | PASS | PASS | PASS | PASS |
| popup-report | PASS | PASS | PASS | PASS | PASS |
| otp-login | PASS | PASS | PASS | PASS | PASS |
| slow-forecast | PASS | PASS | PASS | PASS | PASS |
| **Total** | **14/14** | **14/14** | **14/14** | **14/14** | **14/14** |

| Process (per arm, 14 runs) | opus-skill | opus-bare | sonnet-skill | codex-skill | codex-bare |
|---|:-:|:-:|:-:|:-:|:-:|
| Critical failures | 0 | 0 | 0 | 0 | 0 |
| Median tool calls per task | 8 | 9 | 6.5 | 14.5 | 17 |
| Total tool calls | 116 | 128 | 89 | 214 | 252 |
| `umbra help` lookups (all runs) | 2 | 21 | 1 | 3 | 17 |
| Runs that used `sleep` instead of `wait` | 0 | 2 | 2 | 0 | 0 |
| Median wall time per task | 28 s | 29.5 s | 21.5 s | 41 s | 43 s |

Codex counts each shell command or MCP call as a tool call. Neither Codex arm used the Umbra MCP tools (both drove the CLI), and none fetched the site around the browser with `curl` or the like. In codex-bare, every run read the installed skill (`~/.codex/skills/umbra/SKILL.md`) before its first browser command; nothing in its task prompt mentioned the skill, but the standing `~/.codex/AGENTS.md` rule names it.

## What the eval changed

1. **The skill's safety rules were too blunt.** The first version said "get the user's explicit OK before purchases, posts, submissions, refunds…". The opus-skill refund candidate then stopped and asked for permission, even though the request said "Please refund it", and failed (13/14). The rules are now conditions on the request: do what was asked, ask before consequential actions that are your own idea, and use credentials the request provides. Re-run, the refund passed every time.
2. **`wait --idle` ignored `--timeout`.** An opus-bare candidate's `wait --idle` ran past two minutes and had to be killed. It's fixed in 2dd2701: it now fails at the timeout with a hint to wait for text or a selector.
3. **The harness itself had a bug.** One batch gave three reps of a task the same profile. The agents noticed tabs and sign-ins changing under them and moved to scratch profiles on their own, but the batch was discarded and re-run with a profile per run. The lesson went into the docs: parallel agents need separate profiles (or `--tab`), and cookies on 127.0.0.1 are shared across ports.

## Reading the results

- Umbra is usable without the skill in the prompt: opus-bare passed everything with only `umbra help` in the task prompt, which suggests the CLI's help text holds up for agents (the installed skill was within their reach too, and their transcripts weren't kept to show whether they used it). The skill's value is efficiency and discipline: about 10× fewer help lookups, and none of opus-skill's runs fell back to `sleep`.
- A smaller model with the skill passed everything, with the fewest tool calls.
- Limits:
  - Each arm ran each task once (n = 1 per cell).
  - The tasks were written by this project's own proving lane, not held out from it.
  - Everything ran on one machine, alongside other work.
  - CAPTCHA/handoff behaviour isn't covered.
  - Two model families were tested: Claude (Opus and Sonnet 5.5, in Claude Code) and OpenAI's GPT-6 Sol (in Codex). Codex ran with high reasoning effort, the user's own setting.

- Codex took more tool calls per task than Claude (14.5 and 17 against 6.5 to 9) and about 40 s a task. Its most common command was `snapshot`, about four per task. Its calls rarely went wrong: 2 of its 466 commands failed, and none was repeated back to back.

Decision: promote the revised skill. Codex as installed needs nothing more: the skill in `~/.codex/skills` and the rule in `~/.codex/AGENTS.md` are enough.
