# Assignment 3 · A tool surface that tells the truth, and writes that happen once

Model: `gpt-4o-mini`, through OpenAI's API, the same model as Assignments 1 and 2.
Price line in `pricing.yaml`, read 2026-10-08 from
<https://developers.openai.com/api/docs/pricing>. Every count of writes below comes
from `tracker/tracker.db`, not from the traces.

## Part A · The five definitions

- **Strict tools, optional parameters, nullable or union-typed parameters**
  (`python tools/audit_schemas.py`): **5 strict tools, 0 optional parameters, 6
  nullable or union-typed parameters.** The six are `code_search.path_glob`,
  `code_search.max_results`, `code_search.response_format`,
  `code_read_file.response_format`, `code_read_file.line_range.start` and
  `code_read_file.line_range.end`. Every one is a field that may be absent, written
  as required-and-nullable, which is slide 21's fit: 6 of the 16 Anthropic allows.
- **Does your vendor have a strict mode? What did `make ping` show?** Yes. OpenAI's
  own API has one (`function.strict`), so all five definitions carry
  `"strict": true`. `make ping`'s third call sent all five in one request and the
  vendor accepted it: `3. your 5 definitions were accepted in one request (5 marked
  strict)`, recorded in `runs/_ping.jsonl` with `accepted: true`. So
  `strict.compiles` is `true`. The definitions also fit Anthropic's limits at the
  same time (5 strict tools of 20, 0 optional, 6 unions of 16), so the same five
  would compile there unchanged.
- **Consolidation, in one line:** there is no pair of these five that is always the
  same calls in the same order, so nothing is consolidated; search then read looks
  like one, but the checkpoint has to inspect the search result before a path is
  chosen, and the pull request and the comment have to stay apart precisely because
  the order between them is what makes a failed run recoverable.

The five names are namespaced by prefix, one prefix per service: `issues_read` and
`issues_comment` (the issue tracker), `code_search` and `code_read_file` (the
codebase), `pulls_open_draft` (the pull requests).

### `search_repo`, before and after

| Before | After |
|---|---|
| **Name** `search_repo`. Which service? The repository has an issue tracker, a codebase and a remote; the name says only that something is a repo. | **Name** `code_search`. The `code_` prefix names the codebase, and the issue tracker's two tools and the pull-request tool carry prefixes of their own, so the model never has to guess which system a call touches. |
| **Description** "Search the code." Eighteen characters, and it answers none of the questions a caller actually has: is this a substring match or a regular expression? Over file names or file contents? Case-sensitive? | **Description** 1,400 characters: literal, case-insensitive, line by line, over contents and never over file names, not a query language, and what a short phrase buys over a sentence from the issue. |
| **`query`** "Text to look for." | **`query`** says what changes when it is set: matched case-insensitively as a substring of each line, so each extra word must appear verbatim on the same line, which means a longer query matches fewer lines, not more. That is the misuse the old description invited. |
| **`filter`** "Optional filter expression." A free-text filter with no syntax given. Faced with this a model invents one: SQL-like, Lucene-like, JSON, or plain English, and is usually wrong. | **`path_glob`**, a path glob and nothing else, with its character set spelled out and the explicit denial that it is a query language. It also says what it costs (it hides matches elsewhere) and when to choose it (when the directory is already known), and that `count: 0` under a glob is worth repeating with `null`. |
| **`deep`** "Whether to do a deep search. Default false." A boolean whose two sides are never described, and whose cost is never stated. | **Gone.** The capability it named is not one the task needs, and an argument whose effect nobody can state is an argument a model sets at random. What replaced it is `response_format: concise \| detailed`, a closed enum whose two values are described by what each returns (160 characters of a line, or 600) and when the expensive one is worth it. |
| **`max_results`** "How many results. Default 10." The default is in the description, so a vendor compiling the schema cannot enforce it, and the model must either guess or set it. | **`max_results`** is required and nullable: `null` *is* the default, so a model that has no opinion has a value to pass, and 1 to 20 is stated. The range itself is checked at the boundary, because `minimum` and `maximum` are keywords Azure OpenAI's strict mode rejects. |
| **Result** "A list of matching items." In practice a block of `path:line: text` lines, and `"No matches."` for a query that matched nothing **and for a malformed one**. The two outcomes are indistinguishable. | **Result** a declared JSON object (`result_schema` in the file, validated before the model sees it): `query` echoed, `query_valid`, `path_glob`, `response_format`, `count`, `returned`, `truncated`, `index_age`, and `results` as `{path, line, text}`. A query that matched nothing is a success with `count: 0` and `query_valid: true`; a malformed one is an `is_error` result. `truncated` and `index_age` are fields, not a sentence at the end of a blob. |
| **Examples** none. | **Two**, in the file and rendered into the end of the description: a typical call (one identifier, no glob, the cheap format) and a hard one (a bracket in `query`, a glob in `path_glob`) that settles the case the schema leaves unclear, namely that the same bracket is a character to find in one field and an error in the other. |
| **Cap** nowhere in the definition. | **Stated in the description and reported in the result**: at most `max_results`, at most 20 whatever is asked for, and `truncated: true` whenever `count > returned`. |

## Part B · Errors

Classes, per the lecture: **transient** may be retried, **permanent** will fail
again however often you retry, **ambiguous** is a timeout after the request was
dispatched, where nothing readable says whether the effect happened.

| Tool (role) | Transient | Permanent | Ambiguous |
|---|---|---|---|
| `issues_read` (read_issue) | the issue's file could not be read (`OSError`): retried in the registry, up to twice, below the model | no issue with that number (the error lists the numbers that exist); `number` out of range, missing, a string, or any non-integer; an unknown argument | none: a read has no effect to be unsure about |
| `code_search` (search) | the prebuilt index could not be read (`OSError`): retried below the model | a blank or whitespace-only `query`; a `query` over 4,000 characters; a `path_glob` that is not a glob; `max_results` outside 1 to 20; a `response_format` outside the enum; a missing, wrong-typed or unknown argument | none |
| `code_read_file` (read_file) | the file could not be read (`OSError`): retried below the model | no file at that path, case included (the error names every near match); a file under `config/`; a file that is not text; a `line_range` whose `start` is past its `end`; a blank or over-long `path`; a missing, wrong-typed or unknown argument | none |
| `issues_comment` (comment) | none the caller can see. A connection lost before the request was sent would be transient, but the tracker reports it in the same words as a timeout after the commit, so it cannot be told apart and is counted ambiguous | a blank body; a body over 8,000 characters; no issue with that number; a missing, wrong-typed or unknown argument; a call a hook denied | `TrackerTimeout`: the comment may or may not have been committed |
| `pulls_open_draft` (open_pr) | none the caller can see, for the same reason | a blank `branch` or `title`; a `title` over 200 or a `body` over 8,000 characters; no issue with that number; a missing, wrong-typed or unknown argument; a call a hook denied | `TrackerTimeout`: the pull request may or may not have been opened |

The two write rows are the lecture's point in one line: the tracker's
`timeout_before_commit` and `timeout_after_commit` reach the caller with identical
words, so the caller cannot classify a timeout at all. It is the `attempts` table,
the tracker's own log, that knows which happened, and the harness is not allowed to
read it. That is why a timeout is treated as unknown rather than as failed.

- **Protocol errors: `generic_result`, and why.** A protocol error is an unknown
  tool name or arguments that are not an object: the request itself was malformed,
  and no rewording of arguments can fix it, so handing it to the model produces an
  agent that argues with something no argument can answer. The full error goes to
  `runs/<run_id>.harness.log`, where an operator reads it. I chose
  `generic_result` over `end_run` because the model API requires every tool call to
  be answered before the next request, and a generic answer lets a run that is
  otherwise healthy carry on: an unknown tool in one call of a five-step triage
  should not throw away the four steps that worked. The generic result is 153
  characters, names no tool at all (not the unknown one and not any of mine) and
  carries nothing to repair from, so there is nothing in it to argue with:

  > `Error: that call could not be dispatched. The detail is in the operator log. Call one of the functions offered in this request, with an arguments object.`

  What the operator gets, from a run of `eval/probe_tools.py`:

  > `2026-10-21T11:49:04 run probe-unknown-db31d7 protocol error: no tool named 'repo_delete_branch'; this registry offers ['issues_read', 'code_search', 'code_read_file', 'issues_comment', 'pulls_open_draft']; arguments {"branch": "main"}`

- **The exact text of the `read_file` near-miss error for `src/report.ts`** (501
  characters, repair fact first, so a truncation leaves it actionable):

  > `Error: no file at src/report.ts in the repository. The repository has these near matches, and they are different files: src/Report.ts, src/report.tsx, src/reports.ts. Paths are matched exactly, case included, relative to the repository root, and this tool compares the path with the repository's own file list rather than asking the file system, so a wrong case fails here instead of opening the other file. Copy a path from a search result. This will not succeed with any other spelling of this path.`

  The file system is never asked. `tools/read_file.py` builds the repository's list
  of paths and compares strings, because on a case-insensitive disk
  `src/report.ts` opens `src/Report.ts` and reports success, which is the worst
  shape of failure: a well-formed result that is wrong.

**Retry allocation.** Transient transport failures are retried inside the registry,
twice, which costs latency and not a turn. Everything semantic goes to the model
with a sentence saying whether a retry can work at all: a permanent error ends with
"this will not succeed with any other spelling of this path" or "the same arguments
will be refused again", and the one genuinely transient message ends with "retry
after 30 seconds". An ambiguous failure goes to neither: the three-exit gate in the
registry resolves it without a model call, because a model asked to retry a write
it cannot see is being asked to guess.

## Part C · Writes that may already have happened

- **The key formula** (`tools/__init__.py`, `derive_key`):

  ```
  key = "rm-" + sha256(canonical)[:32]
  canonical = json.dumps({"run_id": ..., "plan_step": ..., "tool": ..., "args": ...},
                         sort_keys=True, separators=(",", ":"))
  ```

  For example:
  `{"args":{"body":"label: bug\nfile: src/api/health.ts","number":4192},"plan_step":"4","run_id":"planned-4192-s01-...","tool":"issues_comment"}`.
  The attempt number is deliberately **not** in it: that is exactly what makes a
  retry a retry rather than a second write. The harness computes it and the model
  never sees it; `idempotency_key` is an argument of each write tool's `run()` and
  appears in no definition file. On a retry the registry re-sends the arguments it
  sent the first time, which is why the draft is written once, before either write,
  and never regenerated: if the model rewrote the comment's text, the hash would
  change and the write would duplicate.

- **How a run whose file is `none` skips the PR:** by a check in the executor
  (`ways/planned.py`, `pull_request`). When the executor reaches the pull-request
  step it first makes sure the draft exists, parses the draft's `file:` line, and
  when that is `none` it records the step as `skipped` and writes a `pr_skipped`
  trace line instead of calling the tool. I chose a check over a plan condition or a
  revision for three reasons: the plan is fixed before the issue is read, so whether
  the file is `none` is not knowable when the plan is written; a check in code costs
  no model call where a revision costs one; and a check cannot be talked out of,
  where a condition the model fills in can. It is also the only one of the three
  that `harness.scripted:DraftNamesNoFile` can test for free, which is how I tested
  it: that model drafts `label: bug / file: none`, and both development runs skipped
  the pull request and posted one comment.

- **One call-record line for a write** (`runs/planned-4191-s03-db6c14.calls.jsonl`,
  the pull request's first attempt, which timed out; its `body` is cut here only to
  fit the page):

  ```json
  {
    "run_id": "planned-4191-s03-db6c14",
    "t": 20.423,
    "tool": "pulls_open_draft",
    "role": "open_pr",
    "args": {
      "number": 4191,
      "branch": "repomind/issue-4191",
      "title": "Fix npm run serve command in README",
      "body": "Issue 4191 reports that the command `npm run serve` as state..."
    },
    "plan_step": 4,
    "key": "rm-e5e938d7a6b781dab1a506475f266ab2",
    "attempt": 1,
    "outcome": "timeout",
    "is_error": true,
    "result": "the tracker did not answer in time",
    "latency_s": 0.013,
    "cost_usd": 0.0
  }
  ```

  The next line of the same file is the same `attempt` and the same `key` with
  `"outcome": "ok"`: the write had in fact committed, and reading it back found it,
  so the run proceeded with what was there rather than writing again. That pair is
  the first exit. The second exit looks like
  `runs/planned-4191-s02-b2f6bd.calls.jsonl`, where attempt 1 timed out and the
  read-back said the comment was not there, so attempt 2 ran with the same key
  `rm-c2c08ae...`; that attempt timed out too, and this time the read-back found
  it, so the run proceeded on one comment. The third exit is below.

**The three exits, tested on the development issues.** Six runs under
`FLAKY_WRITES=1` left exactly one comment each and one pull request each where the
draft named a file; the tracker's `attempts` log recorded five
`timeout_after_commit` and two `timeout_before_commit` among them, and no run left a
duplicate. Six more under `FLAKY_WRITES=1 READBACK_FAIL=1` reached the third exit
four times:

| Run | What the tracker holds | How it ended |
|---|---|---|
| `planned-4192-s01-106db1` | `pr:timeout_after_commit`, 1 PR, **0 comments** | `escalated` |
| `planned-4191-s02-ebeab5` | `pr:written`, `comment:timeout_after_commit`, 1 PR, 1 comment | `escalated` |
| `planned-4192-s02-86fb38` | `pr:written`, `comment:timeout_after_commit`, 1 PR, 1 comment | `escalated` |
| `planned-4192-s03-68545f` | `pr:written`, `comment:timeout_before_commit`, 1 PR, **0 comments** | `escalated` |

The first row is the one the order of the two writes was chosen for: the pull
request committed, could not be confirmed, and the run stopped **before** the
comment. A reader has seen nothing, and a person can close one draft pull request.
Had the comment gone first, that run would have left a public comment it could not
account for.

## Part D · Measure

Both arms are the six evaluation issues twice, 12 runs each, `gpt-4o-mini` through
OpenAI's API, under `FLAKY_WRITES=1`. The control adds `KEY_MODE=uuid VERIFY=off`.
Every number in item 2 comes from `tracker/tracker.db`, read by `eval/count_writes.py`;
the traces are consulted only to say which `run_id` belongs to which arm.

1. **The surface price, with its arithmetic:** counted by difference, with
`eval/price_surface.py`, because a definition's price is not the token count of its
JSON. The vendor rewrites the schema into its own form and charges a tool-use preamble
that appears in no definition. The floor is one message with no tools at all: 29 prompt
tokens. Every figure below is a request's prompt tokens minus that floor.

| | starter | mine | factor |
|---|---:|---:|---:|
| the five together | 191 | 3,604 | 18.9x |
| `issues_read` (was `read_issue`) | 37 | 396 | 10.7x |
| `code_search` (was `search_repo`) | 71 | 907 | 12.8x |
| `code_read_file` (was `read_file`) | 34 | 847 | 24.9x |
| `issues_comment` (was `post_comment`) | 44 | 638 | 14.5x |
| `pulls_open_draft` (was `open_pr`) | 53 | 864 | 16.3x |
| the planner's list, five names and one line each | 113 | 2,414 | 21.4x |

The five measured one at a time sum to 3,652, and the five measured in one request cost
3,604. The gap is 48 tokens. The starter's five sum to 239 against 191 together, which
is the same 48. So the preamble is a fixed charge per request, not per definition, and
measuring a tool alone overstates it by 48 tokens every time. This is the part of the
bill that is invisible in the files: no amount of reading `tools/*.json` finds it.

What a run actually pays. The executor offers one tool per call and the planner the
list of five, so a run's surface is the sum over its calls of what each one carried. The
harness arm made 68 model calls over 12 runs, 5.67 per run, every one carrying
definitions. At $0.15 per million uncached input tokens:

- the starter's definitions: 454.8 tokens per run, **$0.000068**
- mine: 8,708.7 tokens per run, **$0.001306**
- all five of mine on every call, instead of the step's tool alone: 20,422.7 tokens per
  run, **$0.003063**

The rewrite costs 8,708.7 - 454.8 = 8,253.9 extra tokens per run, so
8,253.9 x $0.15 / 1,000,000 = **$0.001238 per run**. That is an upper bound: 47,488 of
the arm's 123,797 prompt tokens, 38 percent, were served from cache at half the rate, so
the true increment is nearer $0.0010. Against a mean run of $0.00187, the definitions are
the largest single item in the bill, and not by a little. The third line is the one worth
keeping in view: the saving from offering the step's tool alone rather than all five,
$0.0018 per run, is larger than everything the rewrite added.

2. **The table, from the tracker:**

| Measure | Your harness | Control |
|---|---:|---:|
| Triage comments per run (should be 1) | **1** | **3** |
| Runs with more than one comment | **0 of 12** | **7 of 12** |
| Open PRs from `repomind/issue-<n>` per run (1 when the file is not `none`, else 0) | **12 of 12 correct** | **12 of 12 correct** |
| Writes that timed out and had in fact committed | 7 | 18 |
| Runs that ended `escalated` | **0** | **5** |
| Triage success (the grounded check from Assignment 1) | 4 of 12 | 2 of 12 |

Underneath those rows, from the tracker's `attempts` log. Both arms set out to make the
same 19 writes: 12 comments, and 7 pull requests on the runs whose triage named a file.

| | attempts | committed | committed then timed out | timed out before committing | rows the tracker holds |
|---|---:|---:|---:|---:|---:|
| your harness | 22 | 19 | 7 | 3 | **19** |
| the control | 33 | 26 | 18 | 7 | **26** |

Nineteen intended writes, nineteen rows. Every one of the harness arm's seven committed
timeouts was read back, recognised, and not written again; every one of its three
uncommitted timeouts was retried under the same key and committed on the retry. The
control's 26 rows are the same 19 writes plus 7 comments it wrote twice, because its key
changed on every attempt, so its check before the write looked for a key nothing had ever
been written under and found nothing to find. Both arms hold 7 pull request rows, and no
duplicates, which is the point of the comparison: a pull request has a natural key the
model did not choose, its branch, and both arms check it. The comment has none, and the
derived key is what supplies one.

The two things the duplicates cost, beyond the duplicates themselves. First,
`planned-4205-s04-1c3168` posted the **right** answer to issue 4205, `question` / `none`,
and then posted it again; the grounded check scores a run with two comments as a failure,
because a run that says a thing twice cannot say which one it meant
(`eval/check_triage.py:8`). That single run is half the triage gap between the arms.
Second, `planned-4201-s04-f7c465` ended `escalated` with its pull request unconfirmed
after three attempts, which stopped the run before the comment, so nothing at all was
posted. All five control escalations are writes that exhausted `MAX_ATTEMPTS`, since with
`VERIFY=off` a timeout is never resolved, only retried blind.

3. **The four shapes of tool misuse:**

**Right tool, wrong arguments. Happened; caught at the boundary.** Nine of 132 tool
calls returned `is_error`. Four were argument faults, and in all four the tool never
ran: `path is empty` in `planned-4204-s01-d98178`;
`line_range.start: required, and missing; line_range.end: required, and missing` in
`planned-4202-s02-e11f30`, where the plan asked for `kind: "range"` and named neither
bound, which is the one absence the harness refuses to invent a value for; and two paths
that do not exist, `src/EmailService.ts` and `src/utils/emailService.ts`, each answered
with the near-miss list. No call reached a tool carrying an argument the schema or the
content rules would have refused.

**Wrong tool, well-formed call. Did not happen: zero `off_plan` events in 24 runs.**
Not luck. The executor offers the step's tool and nothing else, so the only wrong tool
the model can name is one absent from the request, and the registry answers that as a
protocol error whose detail the model never sees. The surface is the control.

**Correct call, result misread. This is the shape still standing, and every failed
triage is an instance of it.** The label was right in 24 of 24 runs. The file was right
in 6. Nothing went wrong at the boundary in the other 18: 33 of 51 searches returned
`count: 0` with `query_valid: true` and no error at all, a correct answer to a badly
chosen question, and the model read the empty result as evidence about the codebase
rather than about its own query. The sharpest case is `planned-4205-s03-dd924d`, which
passed `path_glob: "CHANGELOG.md,README.md,docs/ARCHITECTURE.md,docs/CONFIG.md"`, a
comma-separated list where one pattern belongs. It is a syntactically valid glob, so
`glob_problem()` has nothing to object to; `fnmatch` looks for a file whose name is that
whole string, finds none, and the tool reports `count: 0` as a success, twice. That is
the defect Lecture 3 opens with, surviving the rewrite: the argument is now named
`path_glob` instead of `filter` and the description says what a glob is, and the model
still passed something that is not one. A name and a description narrow the space of
wrong calls; they do not close it. Closing this one needs the tool to answer a glob that
matches no path in the index differently from a query that matches no line, which is work
for the next assignment and not a wording change.

**No call at all. Cannot happen here, by construction:** the plan is data, validated
before the issue is read, and the executor runs its steps whether or not the model would
have chosen them. The run that writes nothing is the escalated run, which is a decision
and is recorded as one.

Two of these four return a well-formed result that is wrong. The boundary caught the
first shape and the plan's fixed tool caught the second, and both were the cheap ones.
What is left is the expensive one, and no part of a tool definition can reach it.

4. **Every prediction in `reports/PREDICTIONS_03.md` that missed, and why:**

Right: no duplicate comment in the harness arm and a maximum of one per run; 7 of 12
control runs with a duplicate against the 5 to 8 predicted, with `comments_per_run_max`
reaching 3; no duplicate pull request in either arm; no escalation in the harness arm;
and `code_search`, through `path_glob`, as the tool the model misuses most.

**Control escalations: predicted 0 to 1, measured 5.** The worst miss, and it came from
reading my own gate too narrowly. I reasoned that `READBACK_FAIL` is off in both arms, so
the third exit is never reached, and concluded that the control therefore has one fewer
way to stop than the harness. The second half does not follow from the first. With
`VERIFY=off` the control never reads a timed-out write back at all, so it cannot resolve
a timeout; it can only retry blind, and after `MAX_ATTEMPTS = 3` unconfirmed attempts the
registry escalates. Turning verification off does not remove a stop, it removes the only
thing that clears one. The harness arm resolved all 10 of its timeouts on the first
read-back and escalated nothing.

**Writes that timed out and had committed: predicted roughly 10 to 16 per arm, and the
arms close. Measured 7 and 18.** Two errors. The count is per attempt, and the arms do
not make the same number of attempts: the control made 33 to the harness's 22, because a
blind retry under a fresh key is always attempted while a read-back that finds the row
ends the matter. More attempts, more timeouts. I also mis-stated the rate: I wrote "about
five in ten commit and then time out", which is the share given a timeout, not the share
of all attempts. The control's 18 of 33 is 55 percent and matches the fixture; the
harness's 7 of 22 is 32 percent, low on a sample this small but not surprising on 22
draws. The prediction that the two arms would be close was wrong for a reason knowable
before the runs: the keys change how many attempts happen, and the fault rate is per
attempt.

**Triage success, harness arm: predicted 5 to 9 of 12, measured 4.** Just outside, and
the direction is informative. I expected the better definitions to help the model find
the right file and they did not: the labels were already right everywhere, and the file
was wrong in 8 of 12 runs for the reason in item 3, which no definition can fix. Issue
4205 was as easy as predicted, both runs correct.

**Triage success, control: predicted the same as the harness within noise, measured 2
against 4.** I wrote that nothing about the keys touches whether the label and the file
are right, which is true of the content and false of the score, because the check counts
comments before it reads one. A duplicate turns a right answer into a failure, and an
unconfirmed pull request stops the run before any answer at all. The keys do not make the
triage worse; they make a correct triage unscoreable. That is the strongest argument in
this report for deriving the key, and it is the one I did not see coming.

**`code_read_file`'s `line_range`, filled under `kind: "whole_file"`: predicted, did not
happen once.** All 19 reads passed `kind: "whole_file"` with both bounds null. The one
`line_range` fault went the other way, and was caught: a plan that asked for
`kind: "range"` and named no bounds. The `kind` enum was read as the thing that decides,
not as decoration.

## AI assistance

I used Gemini and Claude to better understand the starter repository, the assignment handout, and its specifications. I used GitHub Copilot for autocompletion and linting while coding, and I also used Claude Code to draft the skeletons for prompts and documentation. I made sure to manually verify all of these to ensure correctness.
