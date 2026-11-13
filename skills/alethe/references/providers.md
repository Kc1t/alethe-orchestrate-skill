# Provider adapters

Read this reference when choosing a provider, resuming a worker, or defining a custom adapter. The
runner uses argument arrays. An unreadable `.cmd` shim runs through `cmd.exe` only under the
argument restrictions in the Windows section below.

## Built-in adapters

| Provider | Executable | Read-only | Write | Resume | Structured result |
|---|---|---:|---:|---:|---:|
| Codex | `codex` | sandbox `read-only` | sandbox `workspace-write` | `codex exec resume` | JSONL events + final message |
| Claude Code | `claude` | permission mode `plan` | `acceptEdits` | `--resume` | stream JSON |
| Agy / Antigravity | `agy` | mode `plan` | `accept-edits` | `--conversation` | JSON result |
| Kimi Code | `kimi` | none (see below) | prompt mode | `--session` | stream JSON |

`antigravity` is accepted as an alias for `agy` in plans.

Use `provider: auto` for a new worker with no provider-specific model, effort, or session. The
runner selects the first installed provider in the saved `providerOrder` that is not listed in
`conserveProviders` and can run the worker: read-only work never goes to Kimi. Explicit providers
are switched only when `providerSwitch` is `automatic`; otherwise they are honored or validation
asks for a decision.

The runner probes optional flags from local help where needed. CLI behavior changes over time, so
unreadable Claude/Codex safety help refuses the worker (retry or repair the CLI). Readable help
without an optional flag keeps compatibility. Claude uses `--setting-sources user` when available
to exclude repository settings, hooks and allow rules. Check the generated `stderr.log` and `outcome.json` rather than treating process exit alone as
success.

The equivalent new-session command shapes are:

```text
codex --disable multi_agent exec --sandbox read-only|workspace-write --json --output-last-message <result> --cd <cwd> --config mcp_servers={} -
claude --print --output-format stream-json --verbose --permission-mode plan|acceptEdits --disallowedTools Agent,Workflow --strict-mcp-config [--max-budget-usd <cap>]
agy --output-format json --mode plan|accept-edits --print-timeout <seconds>s --add-dir <cwd> --print=<task>
kimi --output-format stream-json --prompt=<task>
```

Codex and Claude receive the task through standard input. Agy and Kimi require it attached to the
prompt flag: Agy's `--print` otherwise swallows the next flag as the prompt and ignores the real
one. Because the prompt is an argument for them, validation refuses a task longer than about 30,000
characters on Windows or 100,000 elsewhere. Keep stdout and stderr in separate files and enforce an
external deadline even when a provider also offers its own timeout.

## What the runner turns off

- **Subagents.** Codex workers run with its `multi_agent` features disabled and Claude workers with
  the `Agent` and `Workflow` tools denied when the installed CLIs advertise those flags. Agy, Kimi and custom
  workers rely on the worker contract. Every worker also runs with `ALETHE_ORCHESTRATE_WORKER=1`,
  and `run` refuses to start inside a worker.
- **MCP servers.** Codex gets an empty `mcp_servers` table and Claude `--strict-mcp-config`, so
  workers skip the startup time and the tool definitions of every server the user configured. In
  one check, a one-line Claude haiku answer took about half the tokens this way. Set `mcp: true` on
  a worker whose brief needs a server.
- **Overspending.** When the installed CLI supports `--max-budget-usd`, a Claude worker gets the
  reported-cost budget left for it through that flag and stops after the call that crosses it.
  Without that flag, the runner's reported-cost gate can only prevent later workers from starting.

Claude `acceptEdits` does not approve every shell command. The runner requests non-interactive
permission handling when the installed CLI supports it; a denied command shows up as
`permissionDenials` in `outcome.json`, and the report warns that the worker's checks may not have
run.

Kimi's prompt mode always runs in Kimi's auto permission mode, which approves every tool, and it
refuses `--plan`, `--yolo` and `--auto` together with `--prompt`. A Kimi `mode: read-only` worker
is therefore refused. Choose Codex, Claude or Agy, or use `mode: write` with `isolate: true`
for a disposable worktree.

Agy read-only reviewers cannot run shell commands: give them the diff as a file to read. The
runner keeps slash expansion enabled in plan mode because agy 1.4.9 ignores `--mode plan` without
it. Denied actions appear as `permissionDenials`; an empty denied response is a permission failure.
Use Codex or Claude when a read-only unit needs shell commands.

## Windows

npm installs CLIs such as `codex`, `claude`, `kimi` and `gemini` as `.cmd` shims. cmd.exe re-parses
every argument of a batch file: it cuts an argument at its first newline and runs whatever follows
an unbalanced quote. The runner reads npm's shim and launches its real target directly, either
`node <script>` or the native executable, and `probe` shows that as `launch`. A batch file it cannot
read runs through `cmd.exe` only when no argument holds a newline, a quote, `&`, `|`, `<`, `>`,
`^`, `%` or `!`. Otherwise it is refused; point the worker at the real executable instead.

## Resuming

Copy a successful worker's `sessionId` into a later worker's `resume` field. Keep the provider the
same. Codex resumes with the sandbox of the new worker's mode and its effort; Claude, Agy and Kimi
keep the settings of the original session. Claude streams its events, so a Claude worker that timed
out or went quiet still leaves its session id for runner strategy `rescue`.

Resume only a closely related follow-up. If no session ID was emitted, create a new worker with a
compact handoff instead.

How providers behave when a session cannot be resumed (measured 2026-11-04):

- Claude: an unknown session id fails in about 2 seconds with a failed worker and no cost.
- Agy: an unknown session id is not an error for the CLI. It starts a new session with a different id and
  does the task with only the resume brief. The runner compares the returned `sessionId` with the one sent
  (Claude and Agy keep it on a real resume) and fails the worker with `failureKind: session_not_found` and
  no handoff status, so it never ends as `DONE`. Its spend is still reported.
- `resume` cannot be combined with `isolate`. To continue an isolated writer, set `cwd` to its worktree
  and `isolate: false`; the worktree survives `NEEDS_CONTEXT`. A removed worktree is refused before the
  worker starts (`cwd is not a directory`).

Claude's `total_cost_usd` and Agy's token usage of a resumed worker are running totals for the whole
conversation, so `report` shows the increment over the earlier turns of the same session and the
budgets (`maxReportedCostUsd`, `maxReportedTokens`) count only what the resumed worker added. When the earlier
turns are not in the report, the worker is marked `incremental: "unconfirmed"`. Claude token counts and the
cost of other providers are not adjusted.

## Custom CLIs

Use `provider: "custom"` with:

- `command`: executable name or explicit executable path
- `args`: argument array; `{cwd}` and `{result}` placeholders are replaced without shell expansion
- `promptTransport`: `stdin` or `argument`
- `outputFormat`: `text`, `json`, or `jsonl`

Custom adapters have no verified read-only support: `mode: read-only` is refused. Use a capable
built-in provider, or `mode: write` with `isolate: true` for a disposable worktree. Enforce
provider-specific restrictions in arguments; custom adapters do not claim resume, token or quota support.
Onboarding records each checked adapter in the playbooks file under `adapters`; build the worker
from that entry.

Give a custom CLI the narrowest tool permission that still lets it write, such as Kiro's
`--trust-tools=fs_read,fs_write` or Copilot's `--allow-tool write`. Known unsafe flags, including
ones that approve every tool such as `--trust-all-tools`, `--allow-all-tools` and `--yolo`, are
rejected unless both plan `allowUnsafe: true` and saved policy `trust.allowUnsafe: true` are set.
Only the user can authorize the policy exception; the plan cannot grant it. Checks include
flag/value pairs, `--flag=value`, config overrides and substituted arguments.

## Unsupported MCP features

Plain CLI workers do not appear on Alethe's orchestration board. The runner cannot provide live
mid-turn steering or approval dialogs. Claude and Codex quota readings are available through
`usage` without MCP; other readings still need Alethe or the user as [playbooks.md](playbooks.md)
describes. Use the `alethe_*` MCP tools when board or steering capabilities matter.
