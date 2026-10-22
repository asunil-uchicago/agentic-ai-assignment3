"""RepoMind's tools, and the registry that runs them.

Each tool is two files with one name: tools/<module>.json, its definition, the only
thing the model sees of it; and tools/<module>.py, the code that runs it. Rename a
tool inside its .json file and keep both file names. tools/roles.yaml says which
tool plays each of RepoMind's five roles (checks/FORMATS.md, section 11.2).

Assignment 3 rewrote the five definitions (Part A) and made this registry the one
place every call passes through. In the order a call meets them:

  1. the tool's name          an unknown name is a protocol error: the full error goes to
                              ctx.harness_log, and the model gets a generic result that
                              names nothing, because no rewording of arguments can fix it
                              (Part B, task 2)
  2. the hooks                each hook(name, tool_input) runs before the tool; it denies
                              the call by returning anything but None, or by raising, and
                              a hook that raises denies too: the gate fails closed
  3. the boundary             every argument is checked against the definition's schema and
                              its content rules, with enum values compared case-insensitively,
                              before anything runs. Strict mode makes a call well formed;
                              it does not make it right (Part A, task 6)
  4. the tool                 a read runs once, with a bounded retry for a transient
                              transport failure; a write goes through the gate below
  5. the result               checked against the shape the definition declares for it,
                              before the model sees it

A write is the hardest case of the lecture, because it may already have happened:

  the key                     derived here, never by the model, from the run's id, the plan
                              step the call executes, and the arguments in canonical form,
                              so every retry of one action carries one key (Part C, task 2)
  check before you execute     one read before every attempt: an open PR from this branch in
                              this run, or a comment with this attempt's key
  the three exits             after a timeout, read the write back. Yes: proceed with what
                              is there. No: retry, with the same key. Cannot tell: stop and
                              ask a person, which is `escalate` on the result

Every attempt of every call, the denied and rejected ones included, leaves a line in
runs/<run_id>.calls.jsonl, written here and not from what the agent says it did.

The registry's interface, which the runner, your ways and the checker use
(checks/FORMATS.md, section 11.3):

    registry = ToolRegistry(ctx)          # ctx is a RunContext, below
    registry.schemas                      # what the vendor gets: name, description, input_schema, strict
    registry.names()                      # the tools' names, in MODULES order
    registry.definition(name)             # the whole definition file, examples included
    registry.hooks.append(hook)           # hook(name, tool_input) runs before every call
    registry.call(name, tool_input, plan_step=None) -> ToolResult

A registry is bound to one run: every write is stamped with that run's ID. Build
one per run with `default_tools(ctx)`.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from harness.roles import WRITES, roles
from harness.schema import errors as schema_errors

TOOLS_DIR = Path(__file__).resolve().parent

# The five modules, in the order the registry offers them. Keep these file names.
MODULES = ("read_issue", "search_repo", "read_file", "post_comment", "open_pr")

# The fields of a definition the vendor gets. Everything else in the file (the
# examples, the result shape, the boundary's content rules) stays here.
SENT = ("name", "description", "input_schema", "strict")

MAX_ATTEMPTS = 3        # of one write, in both of Part D's arms
TRANSIENT_RETRIES = 2   # of a read, below the model: your code retrying is not a turn
ERROR_CHARS = 700       # an execution error, capped with its repair fact first
KEY_PREFIX = "rm"

# A protocol error "is for you" (slide 34). The model gets this and nothing else: it
# names no tool, so there is nothing in it to argue with, and it fits the 200
# characters checks/FORMATS.md allows.
GENERIC_PROTOCOL_RESULT = ("Error: that call could not be dispatched. The detail is in the operator log. "
                           "Call one of the functions offered in this request, with an arguments object.")


class ToolError(Exception):
    """A tool failed in a way the model can be told about. Becomes an is_error result."""


class ProtocolError(Exception):
    """A malformed request: an unknown tool, or arguments that are not an object. For the
    operator, never for the model."""


@dataclass
class ToolResult:
    content: str
    is_error: bool
    latency_s: float
    escalate: bool = False   # a write whose outcome cannot be read back: the third exit


@dataclass
class RunContext:
    run_id: str
    tracker: object                    # harness.tracker.Tracker
    repo_root: object                  # pathlib.Path to fixtures/repo
    calls: object = None               # harness.calls.CallRecord: runs/<run_id>.calls.jsonl
    harness_log: object = None         # pathlib.Path: runs/<run_id>.harness.log, for protocol errors
    switches: dict = field(default_factory=dict)   # harness.switches.current(), as the runner read them


def load_definition(module: str) -> dict:
    return json.loads((TOOLS_DIR / f"{module}.json").read_text())


# -- Part A, task 3: the examples, rendered into the end of the description ---------------

def render_examples(definition: dict) -> str:
    """The description the vendor gets: the definition's own, then its examples. Every
    vendor reads the description, and not every vendor reads an examples field."""
    description = str(definition.get("description") or "")
    examples = definition.get("examples") or []
    if not examples:
        return description
    lines = [description, "", "Examples of calls to this tool, the first a typical one:"]
    for i, example in enumerate(examples, 1):
        lines.append(f"{i}. {json.dumps(example.get('input'), sort_keys=True)}")
        note = str(example.get("note") or "").strip()
        if note:
            lines.append(f"   {note}")
    return "\n".join(lines)


# -- Part C, task 2: the key, derived by the harness and never by the model --------------

def canonical(run_id: str, plan_step, tool: str, args: dict) -> str:
    """The string the key hashes: the run, the plan step, the tool and the arguments,
    keys sorted and no whitespace."""
    return json.dumps({"run_id": run_id, "plan_step": None if plan_step is None else str(plan_step),
                       "tool": tool, "args": args},
                      sort_keys=True, separators=(",", ":"), default=str)


def derive_key(run_id: str, plan_step, tool: str, args: dict) -> str:
    """One action, one key, however many attempts it takes. The attempt number is
    deliberately not in it: that is what makes a retry a retry and not a second write."""
    return f"{KEY_PREFIX}-{hashlib.sha256(canonical(run_id, plan_step, tool, args).encode()).hexdigest()[:32]}"


# -- Part A, task 6: the boundary ---------------------------------------------------------

def _enum_of(spec: dict) -> list | None:
    values = spec.get("enum")
    return values if isinstance(values, list) else None


def normalize_enums(args: dict, schema: dict) -> dict:
    """An enum's capitalisation is not guaranteed, even under strict mode: 'Concise'
    becomes 'concise' here rather than failing three layers down."""
    out = dict(args)
    for name, spec in (schema.get("properties") or {}).items():
        if name not in out or not isinstance(spec, dict):
            continue
        values = _enum_of(spec)
        if values and isinstance(out[name], str):
            match = next((v for v in values if isinstance(v, str) and v.lower() == out[name].lower()), None)
            if match is not None:
                out[name] = match
        elif isinstance(out[name], dict) and isinstance(spec.get("properties"), dict):
            out[name] = normalize_enums(out[name], spec)
    return out


def _at(args: dict, dotted: str):
    """The value at a dotted path, and whether it is there at all."""
    node = args
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def content_problems(definition: dict, args: dict) -> list[str]:
    """The rules a schema strict mode compiles cannot carry: lengths, ranges and blanks
    (tools/audit_limits.yaml forbids them as keywords). Each problem says what would be right."""
    out = []
    for dotted, rule in (definition.get("boundary") or {}).items():
        value, present = _at(args, dotted)
        if not present or value is None:
            continue
        if isinstance(value, str):
            if rule.get("non_blank") and not value.strip():
                out.append(f"{dotted}: blank, where it must carry text")
            cap = rule.get("max_chars")
            if cap is not None and len(value) > cap:
                out.append(f"{dotted}: {len(value)} characters, where at most {cap} are allowed")
        if isinstance(value, int) and not isinstance(value, bool):
            low, high = rule.get("min"), rule.get("max")
            if low is not None and value < low:
                out.append(f"{dotted}: {value}, where the smallest allowed is {low}")
            if high is not None and value > high:
                out.append(f"{dotted}: {value}, where the largest allowed is {high}")
    return out


def boundary_check(definition: dict, tool_input: dict) -> tuple[list[str], dict]:
    """(every problem, the arguments to dispatch). The structure first, from the schema the
    vendor compiled, then the content rules it could not hold."""
    schema = definition.get("input_schema") or {}
    args = normalize_enums(dict(tool_input or {}), schema)
    problems = list(schema_errors(args, schema))
    return problems + content_problems(definition, args), args


def boundary_message(definition: dict, problems: list[str]) -> str:
    """The repair fact first, then the context the model lacks, then the cap."""
    schema = definition.get("input_schema") or {}
    known = ", ".join((schema.get("properties") or {}))
    text = ("Error: " + "; ".join(problems[:8])
            + f". Refused before {definition.get('name')} ran, so nothing was read and nothing was written. "
            + f"It takes exactly these arguments, all required: {known}. "
            + "Correct the arguments named above and call again; the same arguments will be refused again.")
    return text[:ERROR_CHARS]


class ToolRegistry:
    def __init__(self, ctx: RunContext, modules=MODULES):
        self.ctx = ctx
        self.hooks: list = []
        self._tools: dict[str, tuple[dict, object]] = {}
        for module in modules:
            definition = load_definition(module)
            self._tools[definition["name"]] = (definition, importlib.import_module(f"tools.{module}"))
        try:
            self._role_of = {entry["tool"]: role for role, entry in roles().items()}
            self._args_of = {role: entry["args"] for role, entry in roles().items()}
        except Exception:                       # roles.yaml out of step: reads still work
            self._role_of, self._args_of = {}, {}

    # -- what the model and the vendor see ------------------------------------------------
    @property
    def schemas(self) -> list[dict]:
        """The definitions as the vendor gets them, in MODULES order, ready for
        ModelClient.create(tools=...). The examples are rendered into the description."""
        out = []
        for definition, _ in self._tools.values():
            sent = {k: definition[k] for k in SENT if k in definition}
            sent["description"] = render_examples(definition)
            out.append(sent)
        return out

    def names(self) -> list[str]:
        return list(self._tools)

    def definition(self, name: str) -> dict:
        return self._tools[name][0]

    def role_of(self, name: str) -> str | None:
        return self._role_of.get(name)

    # -- the switches (Part C, task 6) -----------------------------------------------------
    @property
    def key_mode(self) -> str:
        return (self.ctx.switches or {}).get("key_mode") or "derived"

    @property
    def verifies(self) -> bool:
        return ((self.ctx.switches or {}).get("verify") or "on") != "off"

    # -- the call record (Part C, task 5) --------------------------------------------------
    def _record(self, *, tool, args, plan_step, key, attempt, outcome, is_error, result, start) -> None:
        if self.ctx.calls is None:
            return
        self.ctx.calls.write(tool=tool, args=args, plan_step=plan_step, key=key, attempt=attempt,
                             outcome=outcome, is_error=is_error, result=str(result),
                             latency_s=time.monotonic() - start, cost_usd=0.0)

    def _log(self, text: str) -> None:
        """A protocol error, written in full where the operator reads it."""
        path = self.ctx.harness_log
        if path is None:
            return
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} run {self.ctx.run_id} protocol error: {text}\n")

    # -- one call ---------------------------------------------------------------------------
    def call(self, name: str, tool_input: dict, plan_step=None) -> ToolResult:
        """Run one tool. Expected failures come back as is_error results; a protocol error
        goes to the operator's log and comes back generic.

        `plan_step` is the id of the plan step the call runs for, from ctx.call_tool,
        or None when the model chose the call (the agent's loop)."""
        start = time.monotonic()
        entry = self._tools.get(name)
        if entry is None:
            return self._protocol(name, tool_input, plan_step, start,
                                  f"no tool named {name!r}; this registry offers {self.names()}")
        if tool_input is not None and not isinstance(tool_input, dict):
            return self._protocol(name, tool_input, plan_step, start,
                                  f"{name} was called with {type(tool_input).__name__}, not an arguments object")
        definition, module = entry
        role = self.role_of(name)
        args = dict(tool_input or {})

        denial = self._denial(name, args)
        if denial is not None:
            message = f"Error: this call was denied before it ran: {denial}"[:ERROR_CHARS]
            self._record(tool=name, args=args, plan_step=plan_step, key=None, attempt=1, outcome="denied",
                         is_error=True, result=message, start=start)
            return ToolResult(message, True, time.monotonic() - start)

        problems, args = boundary_check(definition, args)
        if problems:
            message = boundary_message(definition, problems)
            self._record(tool=name, args=args, plan_step=plan_step, key=None, attempt=1, outcome="rejected",
                         is_error=True, result=message, start=start)
            return ToolResult(message, True, time.monotonic() - start)

        if role in WRITES:
            return self._write(name, definition, module, role, args, plan_step, start)
        return self._read(name, definition, module, args, plan_step, start)

    def _protocol(self, name, tool_input, plan_step, start, detail: str) -> ToolResult:
        self._log(f"{detail}; arguments {json.dumps(tool_input, default=str)[:400]}")
        self._record(tool=name, args=tool_input if isinstance(tool_input, dict) else {"_arguments": str(tool_input)},
                     plan_step=plan_step, key=None, attempt=1, outcome="protocol", is_error=True,
                     result=detail, start=start)
        return ToolResult(GENERIC_PROTOCOL_RESULT, True, time.monotonic() - start)

    def _denial(self, name: str, args: dict) -> str | None:
        """Why a hook denied this call, or None when every hook allowed it. A hook that
        raises denies: the gate fails closed."""
        for hook in list(self.hooks):
            try:
                verdict = hook(name, args)
            except Exception as exc:
                return f"a pre-call check raised {type(exc).__name__}: {exc}"[:200]
            if verdict is not None:
                return str(verdict)[:200]
        return None

    # -- a read: the tool runs once, and a transient transport failure is retried here --------
    def _read(self, name, definition, module, args, plan_step, start) -> ToolResult:
        for attempt in range(1, TRANSIENT_RETRIES + 2):
            try:
                content = module.run(self.ctx, **args)
            except ToolError as exc:
                message = f"Error: {exc}"[:ERROR_CHARS]
                self._record(tool=name, args=args, plan_step=plan_step, key=None, attempt=attempt,
                             outcome="error", is_error=True, result=message, start=start)
                return ToolResult(message, True, time.monotonic() - start)
            except (TypeError, ValueError) as exc:
                message = (f"Error: {name} could not use these arguments: {exc}. "
                           "Correct them and call again.")[:ERROR_CHARS]
                self._record(tool=name, args=args, plan_step=plan_step, key=None, attempt=attempt,
                             outcome="error", is_error=True, result=message, start=start)
                return ToolResult(message, True, time.monotonic() - start)
            except OSError as exc:               # transient: retrying here costs latency, not a turn
                if attempt <= TRANSIENT_RETRIES:
                    self._record(tool=name, args=args, plan_step=plan_step, key=None, attempt=attempt,
                                 outcome="error", is_error=True, result=f"transient: {exc}", start=start)
                    continue
                message = (f"Error: {name} could not reach the codebase after {attempt} tries ({exc}). "
                           "This is a transport failure, not an argument: retry after 30 seconds.")[:ERROR_CHARS]
                self._record(tool=name, args=args, plan_step=plan_step, key=None, attempt=attempt,
                             outcome="error", is_error=True, result=message, start=start)
                return ToolResult(message, True, time.monotonic() - start)
            broken = self._result_problem(definition, content)
            if broken is not None:
                return self._malformed_result(name, definition, args, plan_step, start, broken)
            self._record(tool=name, args=args, plan_step=plan_step, key=None, attempt=attempt,
                         outcome="ok", is_error=False, result=content, start=start)
            return ToolResult(content, False, time.monotonic() - start)

    # -- the result's own shape, checked before the model sees it --------------------------
    def _result_problem(self, definition: dict, content: str) -> str | None:
        shape = definition.get("result_schema")
        if not shape:
            return None
        try:
            value = json.loads(content)
        except (TypeError, ValueError):
            return "the result is not the JSON object its description promises"
        broken = schema_errors(value, shape)
        return broken[0] if broken else None

    def _malformed_result(self, name, definition, args, plan_step, start, broken) -> ToolResult:
        self._log(f"{name} returned a result that breaks the shape it declares: {broken}")
        message = (f"Error: {name} returned a result that does not match the shape it promises ({broken}). "
                   "This is a fault in the tool, not in the call: it will not succeed with any arguments, "
                   "and an operator has been told.")[:ERROR_CHARS]
        self._record(tool=name, args=args, plan_step=plan_step, key=None, attempt=1, outcome="error",
                     is_error=True, result=message, start=start)
        return ToolResult(message, True, time.monotonic() - start)

    # -- a write: the key, the check before it, and the three exits -------------------------
    def _key(self, plan_step, name: str, args: dict) -> str:
        if self.key_mode == "uuid":              # Part D's control: a fresh key on every attempt
            return f"{KEY_PREFIX}-{uuid.uuid4().hex}"
        return derive_key(self.ctx.run_id, plan_step, name, args)

    def _role_arg(self, role: str, role_arg: str, default: str):
        return (self._args_of.get(role) or {}).get(role_arg, default)

    def _existing(self, role: str, args: dict, key: str):
        """The row this write would duplicate, read before the attempt. One read. None when
        there is none, and None when the read itself failed, which is not a licence to
        assume anything: the gate after the attempt decides that."""
        from harness.tracker import ReadbackUnavailable

        try:
            if role == "open_pr":
                branch = str(args.get(self._role_arg("open_pr", "branch", "branch"), ""))
                rows = self.ctx.tracker.prs(self.ctx.run_id, branch=branch, state="open")
                return rows[0] if rows else None
            return self.ctx.tracker.comment_by_key(self.ctx.run_id, key)
        except (ReadbackUnavailable, OSError):
            return None

    def _read_back(self, role: str, args: dict, key: str) -> tuple[str, dict | None]:
        """The second exit's question: did the write happen? yes, no, or cannot tell."""
        from harness.tracker import ReadbackUnavailable

        try:
            if role == "open_pr":
                row = self.ctx.tracker.pr_by_key(self.ctx.run_id, key)
                if row is None:
                    branch = str(args.get(self._role_arg("open_pr", "branch", "branch"), ""))
                    rows = self.ctx.tracker.prs(self.ctx.run_id, branch=branch, state="open")
                    row = rows[0] if rows else None
            else:
                row = self.ctx.tracker.comment_by_key(self.ctx.run_id, key)
        except (ReadbackUnavailable, OSError):
            return "unknown", None
        return ("yes", row) if row else ("no", None)

    @staticmethod
    def _row_result(module, role: str, row: dict, confirmed_by: str) -> str:
        if role == "open_pr":
            return module.result_for(row, confirmed_by, already_open=confirmed_by == "checked_before_write")
        return module.result_for(row, confirmed_by)

    def _write(self, name, definition, module, role, args, plan_step, start) -> ToolResult:
        from harness.tracker import TrackerTimeout

        for attempt in range(1, MAX_ATTEMPTS + 1):
            key = self._key(plan_step, name, args)

            found = self._existing(role, args, key)      # check before you execute: one read
            if found is not None:
                content = self._row_result(module, role, found, "checked_before_write")
                self._record(tool=name, args=args, plan_step=plan_step, key=key, attempt=attempt,
                             outcome="ok", is_error=False, result=content, start=start)
                return ToolResult(content, False, time.monotonic() - start)

            try:
                content = module.run(self.ctx, **args, idempotency_key=key)
            except TrackerTimeout as exc:
                # The ambiguous failure: the write may or may not have happened.
                self._record(tool=name, args=args, plan_step=plan_step, key=key, attempt=attempt,
                             outcome="timeout", is_error=True, result=str(exc), start=start)
                if not self.verifies:                    # Part D's control: retry blind
                    continue
                verdict, row = self._read_back(role, args, key)
                if verdict == "yes":                     # exit 1: it happened. Proceed with it.
                    content = self._row_result(module, role, row, "read_back_after_timeout")
                    self._record(tool=name, args=args, plan_step=plan_step, key=key, attempt=attempt,
                                 outcome="ok", is_error=False, result=content, start=start)
                    return ToolResult(content, False, time.monotonic() - start)
                if verdict == "no":                      # exit 2: it did not. Retry, same key.
                    continue
                return self._unconfirmed(                # exit 3: cannot tell. A person decides.
                    name, args, plan_step, key, attempt, start,
                    f"{name} timed out on attempt {attempt} and the tracker could not be read back, so "
                    "whether the write happened is unknown.")
            except ToolError as exc:
                message = f"Error: {exc}"[:ERROR_CHARS]
                self._record(tool=name, args=args, plan_step=plan_step, key=key, attempt=attempt,
                             outcome="error", is_error=True, result=message, start=start)
                return ToolResult(message, True, time.monotonic() - start)
            except (TypeError, ValueError) as exc:
                message = (f"Error: {name} could not use these arguments: {exc}. "
                           "Correct them and call again.")[:ERROR_CHARS]
                self._record(tool=name, args=args, plan_step=plan_step, key=key, attempt=attempt,
                             outcome="error", is_error=True, result=message, start=start)
                return ToolResult(message, True, time.monotonic() - start)
            broken = self._result_problem(definition, content)
            if broken is not None:
                return self._malformed_result(name, definition, args, plan_step, start, broken)
            self._record(tool=name, args=args, plan_step=plan_step, key=key, attempt=attempt,
                         outcome="ok", is_error=False, result=content, start=start)
            return ToolResult(content, False, time.monotonic() - start)

        return self._unconfirmed(name, args, plan_step, None, MAX_ATTEMPTS, start,
                                 f"{name} was still not confirmed after {MAX_ATTEMPTS} attempts.")

    def _unconfirmed(self, name, args, plan_step, key, attempt, start, why: str) -> ToolResult:
        """The third exit. "I do not know" is a real answer, and escalating beats guessing."""
        self._log(f"write not confirmed: {why} The run is escalated for a person to check.")
        message = (f"Error: {why} Nothing further will be attempted: a retry could duplicate a write that "
                   "already happened. This run is being handed to a person.")[:ERROR_CHARS]
        return ToolResult(message, True, time.monotonic() - start, escalate=True)


def default_tools(ctx: RunContext) -> ToolRegistry:
    return ToolRegistry(ctx)
