# Project workflow regression


<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="../../../assets/eval-mobile.svg">
    <img src="../../../assets/eval.svg" width="100%" alt="Five agent setups each completed all 14 tasks, 70 of 70, with zero critical failures: Claude Opus 5.5 with the skill (median 8 tool calls per task), Claude Opus 5.5 with only umbra help (9), Claude Sonnet 5.5 with the skill (6.5), GPT-6 Sol in Codex with the skill (14.5), and GPT-6 Sol in Codex as installed, with its own skill and no help in the prompt (17).">
  </picture>
</p>

Can a fresh agent get real work done with Umbra? The harness, [`bench/eval/eval.py`](../../../bench/eval/eval.py), gives each of the proving ground's 14 unattended tasks to a fresh, isolated agent: a Claude Code subagent, or a `codex exec` run. Each one gets its own instance of the site, its own working directory and its own Umbra profile, and never sees the graders. The tasks include password and one-time-code sign-in, paginated and filtered tables, a refund behind `confirm()`, a CSV export, a hidden file input, a validated wizard, infinite scroll, a React-controlled input, a contenteditable, shadow DOM, an iframe, a pop-up and late-loading content. Each is graded from the site's server-side state, the answer and any output files.

| Agent | Guidance | Passed | Median tool calls | `umbra help` lookups, all runs | Runs that used `sleep` | Median time per task |
|---|---|:-:|:-:|:-:|:-:|:-:|
<!-- umbra-figure: docs-evaluation-regression-readme-facts-f84d07f5ae -->
<picture>
  <source media="(max-width: 600px)" srcset="../../../assets/docs/docs-evaluation-regression-readme-facts-f84d07f5ae-1-mobile-6c8f72da11.svg">
  <img src="../../../assets/docs/docs-evaluation-regression-readme-facts-f84d07f5ae-1-9b36c09b59.svg" width="100%" alt="Project workflow regression. Exact values and source links in the expandable text below.">
</picture>

<details>
<summary>Read the text, values and source links</summary>

| Claude Opus 5.5 | the skill, in the prompt | 14/14 | 8 | 2 | 0 | 28 s |

</details>
<!-- /umbra-figure -->
<!-- umbra-figure: docs-evaluation-regression-readme-facts-5657e00276 -->
<picture>
  <source media="(max-width: 600px)" srcset="../../../assets/docs/docs-evaluation-regression-readme-facts-5657e00276-1-mobile-89912b1b1b.svg">
  <img src="../../../assets/docs/docs-evaluation-regression-readme-facts-5657e00276-1-102ce4915b.svg" width="100%" alt="Project workflow regression. Exact values and source links in the expandable text below.">
</picture>

<details>
<summary>Read the text, values and source links</summary>

| Claude Opus 5.5 | only `umbra help` | 14/14 | 9 | 21 | 2 | 29.5 s |

</details>
<!-- /umbra-figure -->
<!-- umbra-figure: docs-evaluation-regression-readme-facts-e9e25bb5cc -->
<picture>
  <source media="(max-width: 600px)" srcset="../../../assets/docs/docs-evaluation-regression-readme-facts-e9e25bb5cc-1-mobile-b5b9fbcf39.svg">
  <img src="../../../assets/docs/docs-evaluation-regression-readme-facts-e9e25bb5cc-1-f7c5b4a373.svg" width="100%" alt="Project workflow regression. Exact values and source links in the expandable text below.">
</picture>

<details>
<summary>Read the text, values and source links</summary>

| Claude Sonnet 5.5 | the skill, in the prompt | 14/14 | 6.5 | 1 | 2 | 21.5 s |

</details>
<!-- /umbra-figure -->
<!-- umbra-figure: docs-evaluation-regression-readme-facts-5800972070 -->
<picture>
  <source media="(max-width: 600px)" srcset="../../../assets/docs/docs-evaluation-regression-readme-facts-5800972070-1-mobile-987510bea5.svg">
  <img src="../../../assets/docs/docs-evaluation-regression-readme-facts-5800972070-1-1b6d64a719.svg" width="100%" alt="Project workflow regression. Exact values and source links in the expandable text below.">
</picture>

<details>
<summary>Read the text, values and source links</summary>

| GPT-6 Sol in Codex | the skill, in the prompt | 14/14 | 14.5 | 3 | 0 | 41 s |

</details>
<!-- /umbra-figure -->
<!-- umbra-figure: docs-evaluation-regression-readme-facts-a896e02326 -->
<picture>
  <source media="(max-width: 600px)" srcset="../../../assets/docs/docs-evaluation-regression-readme-facts-a896e02326-1-mobile-93f456abe4.svg">
  <img src="../../../assets/docs/docs-evaluation-regression-readme-facts-a896e02326-1-94756795ac.svg" width="100%" alt="Project workflow regression. Exact values and source links in the expandable text below.">
</picture>

<details>
<summary>Read the text, values and source links</summary>

| GPT-6 Sol in Codex | as installed: nothing in the prompt | 14/14 | 17 | 17 | 0 | 43 s |

</details>
<!-- /umbra-figure -->

<!-- umbra-figure: docs-evaluation-regression-readme-facts-4aa94ec989 -->
<picture>
  <source media="(max-width: 600px)" srcset="../../../assets/docs/docs-evaluation-regression-readme-facts-4aa94ec989-1-mobile-06ca13163a.svg">
  <img src="../../../assets/docs/docs-evaluation-regression-readme-facts-4aa94ec989-1-8b98d45d15.svg" width="100%" alt="Project workflow regression. Exact values and source links in the expandable text below.">
</picture>

<details>
<summary>Read the text, values and source links</summary>

None of the 70 runs had a critical failure: no Safari or AppleScript, no leaving its own profile, no reading the repository's graders, and no other browser or screen tool. For Codex, [`bench/eval/process.py`](../../../bench/eval/process.py) checks every command and tool call in each run's transcript.

</details>
<!-- /umbra-figure -->

What the runs showed:

<!-- umbra-figure: docs-evaluation-regression-readme-facts-75f9988ba9 -->
<picture>
  <source media="(max-width: 600px)" srcset="../../../assets/docs/docs-evaluation-regression-readme-facts-75f9988ba9-1-mobile-3733f4c6c7.svg">
  <img src="../../../assets/docs/docs-evaluation-regression-readme-facts-75f9988ba9-1-51af690516.svg" width="100%" alt="Project workflow regression. Exact values and source links in the expandable text below.">
</picture>

<details>
<summary>Read the text, values and source links</summary>

- **Both model families do the whole job.** Claude and GPT-6 Sol each passed every task, with and without the skill pasted into the prompt: 14 tasks in 5 setups, 70 of 70 runs.

</details>
<!-- /umbra-figure -->
<!-- umbra-figure: docs-evaluation-regression-readme-facts-ff9ce1698b -->
<picture>
  <source media="(max-width: 600px)" srcset="../../../assets/docs/docs-evaluation-regression-readme-facts-ff9ce1698b-1-mobile-25f107e38d.svg">
  <img src="../../../assets/docs/docs-evaluation-regression-readme-facts-ff9ce1698b-1-a663a15e1c.svg" width="100%" alt="Project workflow regression. Exact values and source links in the expandable text below.">
</picture>

<details>
<summary>Read the text, values and source links</summary>

- **An installed skill gets used.** With nothing about the skill in the task prompt, only the standing `AGENTS.md` rule that names it, Codex read its installed `umbra` skill in all 14 runs, and none used `sleep` instead of `wait`. The skill's value is economy: with it in the prompt, agents look things up far less (3 `umbra help` lookups across 14 Codex runs, against 17 without).

</details>
<!-- /umbra-figure -->
- **The skill was fixed by the eval.** An early draft told agents to get the user's OK before refunds and similar actions, so one agent stopped and asked before a refund the user had explicitly requested. The rule now covers only consequential actions that are the agent's own idea, and the rerun passed.

Each agent ran each task once, the tasks were written by this project, and everything ran on one Mac. The 15th task, a CAPTCHA handoff, needs a person at the screen and wasn't run. Every agent ran with this Mac's own setup, where the skill is installed and the global instructions name it, so "only `umbra help`" and "nothing in the prompt" mean nothing about the skill in the task prompt. Codex ran with its usual setup except where isolation required otherwise: its Umbra MCP server pinned to the run's profile, its own browser tool off, and no session history kept. The Claude arms ran an earlier build (0eecdd3) than the Codex arms (30c0835). In between came 12 source changes, including download-aware click, navigation reports for form submits, the `wait --idle` timeout and new help pages, so call counts and times across the two families aren't like for like. The [full report](RESULTS.md) has every task and the process numbers.
