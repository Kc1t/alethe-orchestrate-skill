# Briefs and reviews

Read this reference when writing a worker's task, or a review, validation, judge or panel brief.

## Is the brief ready?

A vague brief is the most expensive part of an orchestration: every worker pays for it, and the
retries pay again. Score it before sending:

| Part | Score |
| --- | --- |
| Task goal | 0 missing, 1 vague, 2 clear, 3 clear and testable |
| Expected result | 0 missing, 1 vague, 2 clear, 3 checkable by a command or a diff |
| Scope | 0 none, 1 a direction, 2 the paths it owns and the ones it must not touch |
| Constraints | 0 none, 1 some, 2 the repository rules and limits that apply |

Below 7, read more or ask the user before sending anything.

## A writer's brief

In this order:

1. One line on where the unit fits in the whole task.
2. The task goal and the definition of done, written as checks that pass or fail.
3. The paths it owns, and what it must leave untouched.
4. Interfaces and decisions from earlier waves that it cannot discover by itself.
5. How you resolved any ambiguity you noticed in the request.
6. The checks to run, with their exact commands.

A brief describes one unit, not the session's history. Point to files instead of pasting them.

The runner appends the worker contract, which ends with a `STATUS:` and a `SUMMARY:` line. Treat
`BLOCKED` and `NEEDS_CONTEXT` as a question back: change the context, the tier or the scope before
sending the unit again, and never resend the same brief to the same model.

## Resolving NEEDS_CONTEXT with an external state file

When a scout or writer stops with `STATUS: NEEDS_CONTEXT`, do not paste a long back-and-forth into
the next wave's brief. Instead:

1. Record the resolved decision in a small state or spec file in the workspace (such as
   `.planning/STATE.md` or `PLAN.md`) and point subsequent workers to that path.
2. Log the plan adjustment with `scripts/orchestrate.py note "<decision added after NEEDS_CONTEXT>" --kind add`.
3. Launch the next wave referencing the updated state file.

## Answering NEEDS_CONTEXT by resuming the worker

When the missing context is a short answer (a decision, a policy, a path), resume the worker that asked
instead of launching a new one with a longer brief:

1. Take the `sessionId` from the worker's outcome and check that it is the same provider.
2. Launch one worker with `resume: "<sessionId>"` and a brief that holds only the answer and "continue
   where you stopped, without rereading what you already read".
3. If the resume ends `failed` (including `failureKind: session_not_found`, when the provider could not find
   the session and started another), or the worker cannot be resumed, launch a new worker with a compact
   handoff.

Resume does not pay when the answer is long (measured break-even near 20 thousand characters, about 6 thousand
tokens), because the answer is written to the session cache at the same price a new worker pays for it. A
resumed worker cannot use `isolate`; a writer that was isolated can be resumed with `cwd` set to its worktree
and `isolate: false`. Providers report the cost of a resumed worker as a running total of the whole
conversation; the report subtracts the earlier turns of the same session.

Measured on a synthetic project (Claude Sonnet, low effort, read-only and writing tasks, 2 to 3 runs each):
the resumed worker's own cost was 28 to 48 percent below a new worker's on small projects and
a chain of two `NEEDS_CONTEXT`, and 12 percent below on a 180-function writing task where the edits dominate the
cost. Quality was the same in every pair.

## Checking a writer's work

- Check the repository, not the worker's report. Its summary and its status are claims.
- Read changes to tests first. A check that turned green because it was weakened is a failure.
- Passing checks are necessary, not sufficient.
- In a checkout with changes, look at `git diff` and the untracked files before any checkout, reset
  or clean.
- Ask for rework with the delta: the finding and the expected behavior, not the whole task again.
- You integrate and commit. Workers never do.

## A reviewer's brief

Hand the reviewer the task, the worker's `result.md` and its `diff.patch` by path. Ask for:

1. The spec first: what is missing, extra or misunderstood. "Cannot verify from the diff" is an
   answer; a guess is not.
2. Then quality: each finding with file:line, a failure scenario and a severity (critical, high,
   medium or low).
3. Nothing else: every line is a finding, a verdict or a check the reviewer ran.

One strong finding is worth more than several weak ones, and a stated rationale never lowers a
finding's severity. Keep reviewers on the standard tier or above. Cheap models miss real defects
and defend planted ones, so they only skip trivial diffs.

## Two-stage review (spec compliance + adversarial quality)

A single reviewer asked to check everything at once often approves clean-looking code that missed a
requirement. For non-trivial diffs, split the review wave into two independent read-only workers
(using `strategy: research` or `parallel` with `maxConcurrent: 2`, since `quality` allows at most
1 worker per wave):

1. **Stage 1 (`spec-validator`):** Checks strictly whether every acceptance criterion in the brief
   or `PLAN.md` / `STATE.md` was implemented without extra or misunderstood scope. Ignores style.
   Ends with `VERDICT: PASS | FAIL | NOT_PROVEN` and `NOT CHECKED: <criteria or none>`.
2. **Stage 2 (`quality-reviewer`):** Performs adversarial code quality and bug review on the diff
   (edge cases, error paths, data loss, race conditions, and weakened tests). Ends with
   `VERDICT: PASS | FAIL | NOT_PROVEN` and `NOT CHECKED: <criteria or none>`.

## Adversarial review

For a change that must not fail, ask the reviewer to look for reasons it will fail, not reasons it
works. Point it at trust boundaries, data loss, retries and idempotency, races and stale state,
empty, null and timeout paths, schema drift and missing observability. Each finding answers: what
can go wrong, why this path is exposed, the likely impact, and the change that lowers the risk.
It approves only when no substantive finding survives.

## Validator verdicts

A validator checks claims it did not make, in a new session: never resume the author's session
for it. It ends with:

```text
VERDICT: PASS | FAIL | NOT_PROVEN
NOT CHECKED: <criteria nobody demonstrated, or none>
```

PASS needs every criterion demonstrated and nothing left unchecked. FAIL means a failure was
shown. NOT_PROVEN means evidence is missing, so run the missing check before fixing anything. The
runner reads the `VERDICT:` line into `outcome.json`, and the report flags FAIL and NOT_PROVEN.

## Re-review

After a fix, review only the fix's diff and mark each earlier finding ADDRESSED or NOT ADDRESSED.
An attempt that leaves the defect in place is not addressed.

## A panel

For a decision rather than a diff, give the same brief to two or three providers, each in its own
session, without showing them each other's answers. Give each a stance: one argues for, one
against, one stays neutral. The one arguing for still names at least one real reason; the one
against does not oppose a sound idea just to disagree; the neutral one reports the real balance,
not an even split. Each ends with its verdict and a confidence out of 10. You then write the
agreement, the disagreement and why, the recommendation and the risks.
