# Predictions, Assignment 3, Part D

These predictions are written and pushed before the first evaluation run under `FLAKY_WRITES`. Model:
`gpt-4o-mini`, through OpenAI's API. Each arm is the six evaluation issues twice,
12 runs.

The tracker's faults, from `harness/tracker.py`: about one write attempt in ten
times out before committing, and about five in ten commit and then time out, so
roughly six attempts in ten come back as a timeout and five of those six happened.

## Duplicate comments

| | Prediction | Reasoning |
|---|---|---|
| My harness | **0 runs with more than one comment; at most 1 comment per run** | Every attempt of the comment carries the same derived key, and the tracker's unique constraint on `key` turns a repeat into a replay rather than a second row. The key never changes, because it hashes the run id, the plan step and the arguments the harness sent the first time, and the draft is written once, before either write, so there are no newly generated arguments to hash. On top of that, `READBACK_FAIL` is off in both arms, so the read-back after a timeout always answers, and a committed write is recognised instead of retried. |
| The control | **5 to 8 of 12 runs with a duplicate comment; `comments_per_run_max` 2, possibly 3** | The control's key is a fresh UUID on every attempt, so its check before the write (`comment_by_key`) looks for a key nothing was ever written under and always finds nothing, and `VERIFY=off` means it never asks whether the timed-out write landed. About half of first attempts commit and then time out; the blind retry then writes a second row under a new key, which the unique constraint cannot stop. That is roughly 0.5 x 0.9 per run from the first attempt alone, so a little over half of 12 runs, with the occasional third row when two attempts in a row commit and time out. |

## Duplicate pull requests

**0 in both arms.** This is the prediction I am most confident about and the one
the control is least able to lose. A pull request has a natural key that the model
did not choose and the harness did not have to invent: its branch,
`repomind/issue-<n>`. Both arms check for an open pull request on that branch in
this run before every attempt, so the control's second attempt finds the pull
request its own first attempt committed and opens nothing. The comment has no such
natural key, and that is exactly what the derived key supplies.

## Runs that end escalated

**My harness: 0 to 2 of 12. The control: 0 to 1 of 12.** `READBACK_FAIL` is off in
both arms, so the third exit is never reached by a write: a timeout is always
resolved by reading it back. Any escalation will come from somewhere else in
Assignment 2's machinery, most likely a planner that cannot produce a plan passing
the new order rule in three attempts. The control, which skips the read-back
entirely, has one fewer way to stop.

## Writes that timed out and had in fact committed

**Roughly 10 to 16 per arm.** Each run makes one or two writes, each attempted once
or twice, so 15 to 25 attempts per arm, about half of which should land in
`attempts` as `timeout_after_commit`. I expect the two arms to be close, since the
fault rate does not depend on the keys.

## Triage success

**My harness: 5 to 9 of 12. The control: the same, within noise.** Nothing about
the keys touches whether the label and the file are right. The second pass is off
(`--critique off` is the default), so a wrong file is posted as readily as a right
one, and the rewritten definitions do not make `gpt-4o-mini` a better reader of
Ledgerline. Issue 4205, whose answer is `question` / `none`, is the one I expect to
be easiest, because a run that finds nothing has a correct answer available to it.

## The tool I expect the model to misuse most

**`code_search`, and within it `path_glob` before `query`.** Three reasons. It is
the only tool with four arguments and the only one whose arguments are not fixed by
the plan. It is the only tool where a wrong-but-well-formed call returns a success:
a glob that matches nothing, or a query copied from the issue's own prose rather
than from the code, comes back with `count: 0` and `query_valid: true`, which is a
correct answer to a badly chosen question and nothing in the result flags it. And
`path_glob`'s old name, `filter`, with a free-text description, is the defect
Lecture 3 opens Section A with: even with the name and the description rewritten, I
expect to see globs over extensions this codebase does not use, such as `**/*.js`
in a TypeScript repository, which is the misuse I already saw once on the
development issues before these runs began.

Second most likely: `code_read_file`'s `line_range`, where a model that has only
ever needed the whole file may fill `start` and `end` under `kind: whole_file`,
which the tool then ignores. That is harmless, but it is a sign the `kind` enum is
being read as decoration rather than as the thing that decides.
