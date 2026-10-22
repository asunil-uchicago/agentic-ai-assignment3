# ARCHITECTURE.md · RepoMind on the seven layers

For each layer: what does this job, and where is it enforced. Cite code as a file
path and line number joined by a colon, inside backticks; a checker opens every
reference (`checks/FORMATS.md`, section 8). A layer held only by a sentence in a
prompt (any file under `prompts/`) is **EMPTY**, and so is a layer nothing does
yet. Status is one of: present, partial (say what is missing), or **EMPTY**. Each
assignment brings this file up to date.

What each layer is for (Lecture 1, slides 42 to 44):

- **Model**: reasoning, choosing tools, generating text.
- **Harness**: runs the loop, builds each prompt, trims old history.
- **Tools**: where the system acts on the world.
- **Memory & State**: what survives a step, a session, a restart.
- **Control & Policy**: what the system may do, and when it must ask.
- **Observability**: a step-by-step record of what it did.
- **Evaluation**: measuring quality, before and after release.

The table below is RepoMind as Assignment 3 leaves it. Tools stops being partial:
every call is checked at the boundary before it runs, and every write carries a key
the harness derived, so a retry is a retry and not a second write. Control & Policy
gains the three-exit gate, which stops the run and asks a person when a write
cannot be read back. Observability gains the call record, written at the tool
boundary and not from what the agent says it did.

| Layer | What does this job | Where it is enforced | Status |
|---|---|---|---|
| Model | The configured model, called only through the metered client | `harness/client.py:120` | present |
| Harness | The loop, with three bounds checked in code; a plan validated before it runs, and an executor that runs only the plan's steps; the plan rule that the comment is last and the pull request right before it | `agent/loop.py:31`, `ways/planned.py:137`, `ways/planned.py:117` | present |
| Tools | Five namespaced tools behind one registry. Every call is validated at the boundary, against the definition's schema and its content rules, before the tool runs; every write carries an idempotency key the harness derives from the run, the plan step and the arguments, never the model | `tools/__init__.py:211`, `tools/__init__.py:148`, `tools/__init__.py:294` | present |
| Memory & State | A run's progress file, outside the model's context; the tracker's database, which stamps every write with its run and accepts each key once | `ways/notes.py:67`, `harness/tracker.py:169` | partial: nothing survives a run but the tracker's rows |
| Control & Policy | A pre-call hook that can deny any call and fails closed when it raises; the three-exit gate, which retries a write with the same key when it did not happen and ends the run `escalated` when it cannot tell; the second pass, which holds a draft that still fails | `tools/__init__.py:337`, `tools/__init__.py:450`, `ways/planned.py:413` | present |
| Observability | One trace line per model call and per tool call, the plan as data before it runs, and one call-record line per attempt of every call, written at the tool boundary | `agent/loop.py:50`, `ways/planned.py:113`, `tools/__init__.py:276` | partial: traces and a call record, no alerts |
| Evaluation | The grounded check reads the tracker, never the agent; Part D's counts read the tracker's database by run | `eval/check_triage.py:6`, `eval/count_writes.py:97` | partial: offline only |

**Empty layers: 0 of 7**, and three are partial.
