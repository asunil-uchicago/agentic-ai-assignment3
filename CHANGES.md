# CHANGES.md

Three lines per assignment: what it added, what it removed, what it constrained.
Removals are logged like additions.

## Assignment 1
- **Added:** a bounded tool loop; four ways to triage an issue (a direct call, a workflow, an agent, and a hybrid of the two); a grounded success check that reads the tracker; a results script
- **Removed:** nothing
- **Constrained:** every agent run to three bounds checked in code (steps, tokens, wall clock); success to what the tracker holds for the run's ID

## Assignment 2
- **Added:** a plan written as data before the issue is read; a validator with four checks; an executor that runs only the plan's steps; one checkpoint that may revise the plan; a second pass before the comment; detectors and a diff over traces; a progress file and a compaction
- **Removed:** the model's choice of the next tool, in the planned way
- **Constrained:** every tool call to a step of a validated plan; revisions to steps not yet done; the comment to drafts that pass the second pass

## Assignment 3
- **Added:** five rewritten tool definitions, namespaced by service, each with two examples, a declared result shape and a stated cap; a boundary validator that checks every argument against the schema and the content rules the schema cannot carry; errors that state what was wrong and what would be right, with a near-miss list matched against the repository's own file list; a draft pull request opened before the comment; idempotency keys derived by the harness from the run, the plan step and the arguments; a check before every write and a three-exit gate after a failed one; a call record, one line per attempt, written at the tool boundary; a pre-call hook that fails closed; every required argument a fixed plan did not write, supplied from the schema's own absent value rather than spent on a call the boundary would refuse; scripts that price the tool surface and count the two arms from the tracker's database
- **Removed:** free-text tool descriptions and the free-form `filter`, `deep` and generic `max_results` arguments; the file system as the authority on whether a path exists; protocol detail from anything the model is shown
- **Constrained:** every call to arguments the boundary accepted, before the tool runs; every write to one key per action, so a retry is a retry and never a second write; every plan to exactly one comment step, last, with exactly one pull-request step right before it; every write to at most three attempts, after which the run is handed to a person; every tool name and argument name to the role table in `tools/roles.yaml`
