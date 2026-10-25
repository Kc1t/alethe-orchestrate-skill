# Changelog

The version is the integer in `skills/alethe/SKILL.md` under `metadata.version`.
Alethe installs and updates the skill by that number.

## Unreleased

Fixed:

- The editor builds a label, a mark and a quota window for any provider named in `providers` or
  `adapters` that it does not know, such as `opencode` in front of NVIDIA models. Before, its
  boxes rendered without a name or icon.

## 19 (2026-10-23)

Added:

- `report --format html` (`/alethe report html`) writes the report as one offline page in the
  orchestration folder and opens it: the totals in one line, a routing map from each wave through
  the lead to the providers it used with each one's share of the tokens, the plan changes, the
  workers, and what is left to do. Without Python the lead fills `assets/report.html` with the
  JSON it counted, and the page works out the totals.
- `new --lead provider:model` records which CLI and model lead the orchestration, the one that
  plans and writes the prompts. Without it, a lead running in Claude Code is detected. `report`,
  `status` and the report page show it, the page at the center of its routing map.

Fixed:

- The editor's YAML reader no longer holds two invisible line-separator characters in its source.

## 18 (2026-10-23)

Added:

- `editor` (`/alethe editor`) writes the playbooks as one offline flow page and opens it: each
  playbook as a flowchart with its conditions, fallbacks and resolved models, profile switches, the
  latest quota readings as the `now` preset, simulated quota, and YAML to copy or download after an
  edit. The page reads the YAML itself, so without Python the lead fills `assets/editor.html` from
  the shell.

## 17 (2026-10-22)

Added:

- `usage` reads real quota without Alethe: Claude's windows from each Claude worker's rate-limit
  event, Codex's from its own session files. Before every wave the lead checks it and, under any
  goal or profile, moves work off a provider that is running out, telling the user in one line
  with the numbers and reset time.
- A quota stop line, `quota.stopAt` (95%, 90% under savings, 98% under aggressive): `run` refuses
  a wave on a provider at or past it and skips queued workers once a wave crosses it, unless the
  user agreed to `--allow-low-quota`. When moving the work would lower the tier it needs, every
  provider is low, or the forecast passes the line, the lead stops and asks with options: wait for
  the reset, another provider, a cheaper tier for some units, only the units that fit, or another
  CLI or account.
- `dry-run` shows `quotaForecast`, what the wave may take of each window, learned from what past
  waves took. `usage --refresh-claude` reads Claude's quota now with a one-line Haiku call.
- `models` and a bundled `prices.json` with official list prices, each with its source and date.
  A model with no price or a price older than 60 days is looked up on the vendor's pricing page,
  never guessed, and saved to `alethe-prices.json` with the provider's billing. `dry-run` shows a
  labeled list-price estimate; the report still shows only what CLIs reported.
- `playbook --pick` and `playbook <id>` print only what the lead needs, instead of the whole
  600-line file.
- `run --detach` and `wait` keep a wave alive past the shell tool's timeout.

Changed:

- The playbooks file is the single source of model tiers: the runner resolves cheap, standard and
  strong from it and the active profile, so `economy` and the savings profile now take effect.
  `policy.modelTiers` still wins when set and `doctor` suggests moving it. `validate` and `dry-run`
  warn when a worker would run on a CLI's default model, and a broken playbooks copy is refused.
- Cache reads are reported apart and no longer count toward token budgets or the Value split.
  Codex usage is summed across turns and Claude's cost per model is kept.
- A short glossary separates playbook, profile, goal and runner strategy.
- One skill instead of two: the skill is now `alethe` in `skills/alethe`, and the separate `/alethe`
  shortcut is gone, so installers no longer ask which of the two to install. The plugin keeps the
  name `alethe-orchestrate`.

Fixed:

- A runner killed by its shell no longer leaves workers running: signals stop the wave, Windows
  workers run in a Job Object, spend is written as it happens, and `status`, `report` and `stop`
  detect an interrupted runner and kill its leftover workers.
- `seedPaths` refuses globs and pathspec magic and copies untracked files inside a tracked folder.
- The prompt hook reads UTF-8, so projects with accented paths keep their reminder.
- Windows file replacement retries while another process holds the file, git calls have
  timeouts, the Claude cost cap is recorded only when it was passed, Agy is no longer killed as
  idle, and `--run-dir` no longer makes a shared folder current.
- Docs matched to the code: `mode: read-only`, the `report` default, every wave status and the
  policy budget fields.

## 16 (2026-10-21)

Added:

- `/alethe` is now a skill of its own, so it comes with `npx skills add` and works in every agent,
  not only in the Claude Code plugin. It replaces the plugin command of the same name.

## 15 (2026-10-21)

Added:

- `mode on --everywhere` keeps orchestration on in every project with no expiry, so the user never
  has to call the skill. `mode off` in a project turns it off there only, and a project mode wins
  over the default. Setup offers it once, with the goal of the active profile.

Changed:

- The skill's description tells agents to use it proactively when a request splits into bounded
  independent units, so it comes into play without a command in agents that have no prompt hook.

## 14 (2026-10-21)

Added:

- Python is optional. Without it, or when the user asks, the lead follows `references/manual.md`:
  it detects the CLIs, offers two to four options, starts the workers from its own shell with the
  same flags the runner uses, and writes `report.md` from what each CLI reported. Limits are
  counted by the lead after each wave instead of enforced, and the preview diagram is Mermaid.

## 13 (2026-10-19)

Added:

- `/alethe` in Claude Code, and short commands after the skill's name elsewhere: `report`,
  `status`, `list`, `stop`, `skip`, `switch`, `freeform`, `budget`, `mark`, `setup` and `help`.
- `new "<title>"` starts an orchestration folder and makes it current, and `run --in current`
  adds the next wave. `report`, `status`, `budget`, `note`, `stop` and `mark` then need no folder,
  and `mark` needs no run.
- `list` shows recent orchestrations with their waves and spend, and `use` makes one current again.
- `stop` ends the running workers of an orchestration and skips the queued ones.
- `note` records a change to the plan. The report lists them under "Changes to the plan" and
  `status` shows the latest.
- `mode on` keeps orchestration on for a project with a goal (`save`, `balanced`, `max`), an
  optional `careful` level and the user's standing rules, until `mode off` or 12 idle hours. The
  Claude Code plugin's prompt hook reminds the agent while it is on, so the user calls the skill
  once.
- The skill joins mid-task and inside other workflows such as spec-driven development, taking the
  work so far as its plan, and runs with the CLIs it finds when nothing is configured yet.

Changed:

- Playbooks are where the lead starts, not a contract. Between waves it can skip, repeat or add a
  phase, do a step itself, switch playbooks or go freeform, and the user can ask for any of these.

## 12 (2026-10-19)

Fixed:

- On Windows, npm installs CLIs as `.cmd` shims, and cmd.exe cut a prompt passed as an argument at
  its first newline. Kimi workers lost everything after the first line of their task, including
  the worker contract, and a quote in a task could run another command. The runner now launches
  the shim's real target directly, and refuses risky arguments for a batch file it cannot read.
- Kimi write workers failed at startup: Kimi refuses `--yolo` with `--prompt`. Kimi's prompt mode
  always approves every tool, so `auto` no longer sends read-only work to Kimi.
- Codex resumed sessions without the worker's sandbox or effort.
- A Claude worker that timed out left no session id to resume; Claude now streams its events.
- A large prompt sent through stdin could block the runner before any deadline applied.
- On macOS and Linux, a child process that ignored the stop signal survived a timeout.
- A writer in a repository with no commits lost its result; it now succeeds with no diff.
- A stray non-JSON line made a worker's whole output unreadable.
- Workers whose CLI prints plain text now get a `result.md` too.
- The saved-policy note said Alethe erased budgets and model tiers, which it no longer does.

Added:

- `budget <folder>` sets limits on calls, reported cost and reported tokens for every wave of an
  orchestration, enforced by each run.
- Plan budgets take `maxReportedTokens`, the limit that covers CLIs that report no cost. Claude
  workers also get their share of the cost limit as `--max-budget-usd`.
- Codex and Claude workers cannot start subagents, and start without MCP servers unless a worker
  sets `mcp: true`. Every worker runs with `ALETHE_ORCHESTRATE_WORKER=1`, and `run` refuses to start
  inside one.
- Workers end with a `STATUS:` and a `SUMMARY:` line, and validators with a `VERDICT:` line. The
  runner reads them, and the report lists blocked, doubtful and failed work with what to do next.
- A read-only tripwire compares the checkout before and after each read-only worker, and a writer
  in the main checkout records which files already had changes.
- Each outcome has a `failureKind`, such as `rate_limited` or `auth_required`, read from the logs,
  plus the tools Claude denied and the models it billed.
- `mark` records whether a result was used, partly used or discarded; the report then splits the
  spend between them.
- `status <folder>` prints where an orchestration stands, for picking it up after compaction.
- `idleTimeoutSeconds` stops a worker that prints nothing for that long; `state.json` shows each
  running worker's output size and idle time.
- `validate` and `dry-run` warn about blind cost limits, unpinned models, briefs too small to
  delegate, Kimi read-only work, MCP mentions and writers in a checkout with uncommitted changes.
- `doctor` says whether each CLI is logged in.
- `references/briefs.md` covers writing a brief, reviews, adversarial reviews, validator verdicts
  and panels. Playbooks gain a `risky` condition, and review-gate asks for a verdict.
- The repository is also a Codex plugin marketplace.

## 11 (2026-10-18)

First version published from its own repository. Earlier versions shipped inside Alethe.

- `report` explains what an orchestration did and why: each worker's role, where it ran, how long
  it took, the tokens and cost its CLI reported, what it changed, and what is left to review. It
  reads one run or a folder with one run per wave, as Markdown or JSON, and never estimates a
  number a CLI did not report.
- Plans take optional `title`, `why`, `playbook` and `phase` notes, plus a `why` per worker, for
  the report. They never reach the workers.
- Every run saves its resolved plan as `plan.json`, without the task text.
- Worktrees under `.alethe/worktrees/` are added to the local `.git/info/exclude`, so they no
  longer show in `git status` or get staged by `git add -A`.
- Runs in folders with custom names get their own run id, so two orchestrations that reuse a wave
  name no longer collide on worktree paths.
- Output with non-ASCII text no longer fails when it goes through a pipe on Windows.
- `--version` and `doctor` show the skill version.

## 8 to 10 (inside Alethe)

- Setup takes inventory of the CLIs, models and routes you have, proposes base, savings and
  aggressive profiles, and saves them only after you approve.
- Strategy playbooks with conditions, spare-quota rules, `dry-run --write` and an SVG
  architecture preview.
- Flags that approve every tool in a custom CLI, such as `--trust-all-tools`, `--allow-all-tools`
  and `--yolo`, need `allowUnsafe`.
