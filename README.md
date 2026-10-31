<a id="readme-top"></a>

<br />
<div align="center">
  <a href="https://github.com/Kc1t/alethe-orchestrate-skill">
    <img src="./docs/assets/alethe.png" alt="Alethe logo" width="120">
  </a>

  <h1 align="center">alethe-orchestrate</h1>

  <p align="center"><b>Orchestrate coding agents, and prove it was worth it.</b></p>

  <p align="center">
    <a href="https://github.com/Kc1t/alethe-orchestrate-skill/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Kc1t/alethe-orchestrate-skill/actions/workflows/ci.yml/badge.svg"></a>
    <a href="./CHANGELOG.md"><img alt="Skill version" src="https://img.shields.io/badge/skill-v20-4f46e5?style=flat-square"></a>
    <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-blue?style=flat-square"></a>
    <img alt="Python 3.9+ optional" src="https://img.shields.io/badge/python-3.9%2B%20optional-3776ab?style=flat-square&logo=python&logoColor=white">
    <img alt="Windows, macOS and Linux" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555?style=flat-square">
    <a href="https://agentskills.io"><img alt="Agent Skill" src="https://img.shields.io/badge/Agent%20Skill-SKILL.md-111?style=flat-square"></a>
  </p>

  <p align="center"><b>English</b> · <a href="./README.pt-BR.md">Português (BR)</a></p>
</div>

## Install

```sh
npx skills add Kc1t/alethe-orchestrate-skill
```

<details>
<summary>Claude Code plugin, Codex plugin or by hand</summary>

```text
/plugin marketplace add Kc1t/alethe-orchestrate-skill
/plugin install alethe-orchestrate@alethe
```

```sh
codex plugin marketplace add Kc1t/alethe-orchestrate-skill
codex plugin add alethe-orchestrate@alethe
```

Or copy `skills/alethe` into `~/.claude/skills/`, `~/.codex/skills/` or
`~/.agents/skills/`.

</details>

## Getting Started

1. **Install** with the command above.
2. **`/alethe setup`** checks which agent CLIs you have and whether they are logged in, lists their models, and proposes three profiles: base, savings and aggressive. Nothing is saved until you approve.
3. **`/alethe always`** (optional) keeps it on in every project, so you never type `/alethe` again.
4. **Ask for the work**: `/alethe move the billing endpoints to v2`, or just ask in plain words while it is on. The lead picks a playbook, previews the plan and runs the workers.
5. **`/alethe status`** while it runs, **`/alethe stop`** to stop it.
6. **`/alethe report`** when it is done: what each worker did, why, and what it cost. **`/alethe mark <worker> used`** records what you kept.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/how-it-works-dark.svg">
    <img src="./docs/assets/drawings/how-it-works-light.svg" alt="You ask your agent to orchestrate a refactor. The lead agent picks a playbook, writes the briefs and dry-runs the plan, then orchestrate.py runs one wave of workers: Claude Code on haiku, read-only; Codex at low effort, writing in its own worktree; Antigravity, read-only. They leave result.md, diff.patch and outcome.json while full transcripts stay on disk. The lead reads only the results, checks, integrates and marks them, runs the next wave, and finally writes the orchestration report" width="900">
  </picture>
</p>

## Commands

Type them after `/alethe`.

| Command | What it does |
| --- | --- |
| `setup` | Checks your CLIs and sets everything up |
| `<task>` | Orchestrates the task |
| `always` | Keeps it on in every project |
| `freestyle` | Works as usual, only watches cost and suggests cheaper models |
| `status` | Where it stands |
| `stop` | Stops the workers |
| `report` | What each worker did and cost |
| `off` | Turns it off here |

`/alethe help` lists the rest.

## Keep It On

With `always` or `on`, every prompt reminds the lead of your goal and rules, and it decides per request whether to hand work off.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/stay-on-dark.svg">
    <img src="./docs/assets/drawings/stay-on-light.svg" alt="A conversation. The first message says /alethe from now on, be careful with the database, and the lead turns the mode on with careful and keeps the words as a rule. The next messages need no /alethe: a hook reminds the lead of the goal, care and rules each time. It fixes a typo itself because it is too small to hand off, orchestrates moving 40 endpoints with cheap scouts and writers in worktrees, and answers how is it going with a short status. It stays on for the project until /alethe off or 12 hours without a prompt" width="820">
  </picture>
</p>

## Join Mid-Task

It joins halfway through another skill's workflow: the spec and the diff so far become the plan.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/mid-task-dark.svg">
    <img src="./docs/assets/drawings/mid-task-light.svg" alt="A tasks.md from a spec-driven workflow with tasks 1 to 3 done. /alethe joins at task 4. The lead takes the spec and the diff so far as the plan and sends tasks 4 and 5 out: four Codex writers with one worktree per group, two Claude haiku workers fixing the tests, and the coupled part stays with the lead. Task 6, the review gate, stays with the original workflow, whose checks become the gates" width="860">
  </picture>
</p>

## Change Course

A playbook is a starting point. `skip`, `add`, `switch` and `freeform` change it between waves, and the report lists every change with its reason.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/change-course-dark.svg">
    <img src="./docs/assets/drawings/change-course-light.svg" alt="Top row, dashed: the scout-then-build playbook, scout, plan, build, review. Bottom row, what ran: scout, plan, a freeform wave to fix the build script because the bug was there, build, and review crossed out because the diff was three lines. The journal.jsonl records each change with its reason, and the report shows them under Changes to the plan" width="820">
  </picture>
</p>

## Playbooks

| Playbook | Use it when | What it saves |
|---|---|---|
| `direct` | The task is narrow and clear, up to about 3 files | All orchestration overhead |
| `cascade` | A mechanical change with an automatic check | Most of the cost, while retries stay rare |
| `scout-then-build` | Unfamiliar code with much to read and little to write | Your context: scouts return summaries, not files |
| `plan-big-execute-small` | Up to about 10 independent reads, audits or checks | The strong tier only decomposes and synthesizes |
| `audit-at-scale` | Dozens or hundreds of similar items | The volume runs on the cheap tier |
| `parallel-worktrees` | Independent modules with disjoint files | Wall time, not tokens |
| `plan-execute` | One feature built in sequential steps | The strong tier is billed only for planning |
| `advisor-escalation` | A long mechanical task with a few hard calls | A strong advisor only at decision points |
| `best-of-n` | A hard, high-stakes fix worth several attempts | Nothing: it buys reliability |
| `review-gate` | A finished diff before merge | Strong review only on diffs worth reviewing |
| `complete` | Anything else; the conditions decide per unit | Each unit runs on the cheapest role that fits |

## Agents

| Agent | CLI | Read-only work | Writing | Resume | Reports |
|:-:|---|---|---|:-:|---|
| <img src="./docs/assets/agents/claude-code.png" width="28" height="28" alt=""><br>**Claude Code** | `claude` | `plan` permission mode | `acceptEdits` | ✓ | Tokens and cost |
| <img src="./docs/assets/agents/codex.png" width="28" height="28" alt=""><br>**Codex** | `codex` | `read-only` sandbox | `workspace-write` sandbox | ✓ | Tokens |
| <img src="./docs/assets/agents/antigravity.png" width="28" height="28" alt=""><br>**Antigravity** | `agy` | `plan` mode | `accept-edits` mode | ✓ | Tokens |
| <picture><source media="(prefers-color-scheme: dark)" srcset="./docs/assets/agents/kimi-dark.svg"><img src="./docs/assets/agents/kimi-light.svg" width="28" height="28" alt=""></picture><br>**Kimi Code** | `kimi` | Contract, checked afterwards | Prompt mode, in its own worktree | ✓ | Nothing |
| <picture><source media="(prefers-color-scheme: dark)" srcset="./docs/assets/agents/shell-dark.svg"><img src="./docs/assets/agents/shell-light.svg" width="28" height="28" alt=""></picture><br>**Any other CLI** | your adapter | Contract, checked afterwards | Its narrowest write flag | | Whatever its output carries |

Any other CLI with a non-interactive mode, such as Kiro, Copilot CLI or Gemini CLI, runs as a custom worker.

## Why It's Different

- **Real spend, never estimated:** tokens and cost as each CLI reported them, split between used and discarded results.
- **Enforced limits:** budgets per wave and per orchestration, a hard cost cap on Claude workers, no subagents.
- **Never runs your quota dry:** it reads Claude and Codex usage before every wave, refuses to start workers past a stop line, and asks you, with options, before it would spend past it.
- **Lean, checked workers:** no MCP servers in Codex and Claude workers, and git catches a read-only worker that wrote.
- **Easy to audit:** Python's standard library only, no shell, no network, no install scripts.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/drawings/savings-dark.svg">
    <img src="./docs/assets/drawings/savings-light.svg" alt="Two hand-drawn bar charts. Left: one Claude haiku worker on the same one-line task used 76,160 tokens and $0.16 with version 11, and 38,157 tokens and $0.08 with version 12. Right: in a real two-worker run, 5.0 KB of worker transcripts stayed on disk while the lead read 595 bytes of results" width="820">
  </picture>
</p>

## The Report

`/alethe report html` opens the same report as a page: a routing map from each wave through the
lead to the providers it used, with each one's share of the tokens, then the workers and what is
left to do.

> **Orchestration report**
>
> **Check the report on real CLIs**, playbook `research`, 1 wave, 2026-10-18 21:20 to 21:20
>
> - **Result:** 2 of 2 workers succeeded
> - **Time:** 16s running, 29s of worker time
> - **Tokens:** 108,820 reported, 65,836 of them from cache
> - **Cost:** $0.16 reported by claude; codex reports no cost
> - **Lead context:** 5.0 KB of worker transcripts stayed on disk, 595 B of results to read
>
> | Worker | Role | Ran on | Mode | Status | Time | Tokens | Cost |
> | --- | --- | --- | --- | --- | --- | --- | --- |
> | `claude-haiku` | cheapest Claude model for a one-line answer | claude, haiku | read | succeeded | 12s | 76,160 | $0.16 |
> | `codex-low` | default Codex model at low effort | codex, default model, low | read | succeeded | 16s | 32,660 | - |

<details>
<summary>Runner commands and files</summary>

Your agent runs these for you, from `skills/alethe/scripts/orchestrate.py`:

| Stage | Command | What it does |
| --- | --- | --- |
| Setup | `doctor`, `playbook`, `editor`, `policy`, `mode` | Checks the CLIs, prints, shows or edits the playbooks and preferences, keeps it on |
| Plan | `scaffold`, `validate`, `dry-run` | Starts, checks and previews a plan |
| Run | `new`, `run --in current`, `budget`, `note`, `stop` | Runs the waves of one orchestration |
| After | `status`, `mark`, `report`, `list`, `use` | Explains and values what ran |

The policy and your playbooks live in `%APPDATA%\Alethe\` on Windows and `~/.config/alethe/`
elsewhere, with one folder per orchestration under `orchestrations/`:

```text
<folder>/
├── budget.json              limits for every wave, optional
├── 01-explore/
│   ├── state.json           the batch at a glance, live while it runs
│   ├── plan.json            the resolved plan, without task text
│   └── workers/<id>/
│       ├── prompt.txt       the brief the worker received
│       ├── result.md        its final answer
│       ├── outcome.json     status, failure kind, metrics, tripwire
│       ├── diff.patch       what a writer changed
│       ├── mark.json        used, partial or discarded, optional
│       └── stdout.log, stderr.log
└── 02-build/
```

</details>

## Video

<table>
  <tr>
    <th width="38%">Video</th>
    <th>What you will see</th>
  </tr>
  <tr>
    <td>
      <a href="https://www.youtube.com/watch?v=8jvrucR7QCU&amp;t=54s">
        <img src="https://i.ytimg.com/vi/8jvrucR7QCU/hqdefault.jpg" alt="Stop Using One AI Agent at a Time: Orchestrate AI Agents" width="100%">
      </a>
    </td>
    <td>
      <strong><a href="https://www.youtube.com/watch?v=8jvrucR7QCU&amp;t=54s">Stop Using One AI Agent at a Time: Orchestrate AI Agents</a></strong>
      <br><br>
      A practical introduction to coordinating several AI coding agents, and to the git worktree
      isolation that keeps them out of each other's way.
      <br><br>
      <sub>Kauã Miguel - Dev · Portuguese</sub>
    </td>
  </tr>
</table>

## Example Playbook

`scout-then-build`: cheap read-only scouts map the code, then one worker writes the change.
`/alethe editor` opens your own playbooks this way, as one offline page where you can switch
profiles, simulate quota and edit a step, then copy the YAML back. It works without Python too.

<p align="center">
  <img src="./docs/assets/playbook-scout-then-build.png" alt="The scout-then-build playbook as a flow: planner, four scouts, a checker when they disagree, planner, builder, and a fixer when checks fail" width="100%">
</p>

<details>
<summary>The YAML behind it</summary>

```yaml
playbooks:
  scout-then-build:
    name: Scout, then build
    goal: Cheap read-only scouts map the code; one standard worker writes the change.
    pickWhen: Unfamiliar code with much to read and little to write.
    saves: Scouts hand back short summaries instead of raw files filling your context.
    avoidWhen: You already know which files change.
    next: review-gate
    phases:
      - id: brief
        role: planner
        who: lead
        tier: strong
        readOnly: true
        does: Name the areas to map and send one scout per area.
      - id: scout
        role: scout
        tier: cheap
        readOnly: true
        workers: 4
        runner: research
        does: One area per scout; keep each slice well under 100K tokens.
        returns: A summary under 1.5K tokens with file:line pointers.
        gate: The scout reports agree.
      - id: settle
        role: checker
        tier: standard
        effort: high
        readOnly: true
        runner: economy
        after: scout
        if: conflict
        then: recheck
        does: Settle only the contradiction, from the cited lines.
      - id: plan
        role: planner
        who: lead
        tier: strong
        readOnly: true
        does: Turn the summaries into one concrete change plan.
      - id: build
        role: builder
        tier: standard
        readOnly: false
        runner: economy
        does: Implement the plan.
        returns: Changed files, checks run, failures and risks.
        gate: Checks pass.
      - id: fix
        role: fixer
        tier: strong
        readOnly: false
        runner: economy
        after: build
        if: checks-failed
        then: recheck
        does: Fix from the failure excerpt and the diff.
```

</details>

## License

MIT, see [`LICENSE`](LICENSE). The **Alethe** name and logo and the vendor marks belong to their owners. Part of [Alethe](https://github.com/Kc1t/alethe-agents) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/Kc1t/alethe-orchestrate-skill/issues)

<p align="right">(<a href="#readme-top">back to top</a>)</p>
