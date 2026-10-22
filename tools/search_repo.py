"""code_search: literal, case-insensitive text search over the private codebase.

Its definition is search_repo.json. It reads a prebuilt index,
fixtures/search_index/index.json, of every text file under fixtures/repo except
config/, and the index records when it was built.

Assignment 3, Part A. The result is a JSON object, not a line of prose, so that a
search which matched nothing can be told from one that was malformed in the raw
response and not only in the description (slide 15):

  matched nothing    a success: query_valid true, count 0, results []
  malformed          a tool execution error, with a repair fact

Malformed is three things, and each is reported separately. A blank query and a
query past the cap are caught at the boundary, before run() is reached, because
they are length rules (Part A, task 6); a path_glob that is not a glob is caught
here, because what counts as a glob is this tool's own business. `truncated` and
`index_age` are fields of the result, from what the index reports.
"""

from __future__ import annotations

import fnmatch
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

EXCLUDED_DIRS = {"config", "node_modules", ".git"}
HARD_CAP = 20            # at most this many hits, whatever max_results asks for
DEFAULT_RESULTS = 10     # what null means
MAX_QUERY_CHARS = 4000   # as the description states; enforced at the boundary
EXCERPT = {"concise": 160, "detailed": 600}
DEFAULT_FORMAT = "concise"
INDEX = "fixtures/search_index/index.json"
GLOB_CHARS = re.compile(r"^[A-Za-z0-9_.\-/*?\[\]{},]+$")


def glob_problem(pattern: str) -> str | None:
    """Why `pattern` is not a valid path glob, or None when it is one."""
    if pattern.startswith("/"):
        return "a glob is relative to the repository root; it cannot start with /"
    if ".." in pattern.split("/"):
        return "a glob cannot leave the repository (..)"
    if not GLOB_CHARS.match(pattern):
        return "a glob uses letters, digits, / . _ - and the wildcards * ? [ ] { }; no spaces, colons or quotes"
    depth = 0
    for ch in pattern:
        depth += {"[": 1, "]": -1}.get(ch, 0)
        if depth not in (0, 1):
            return "a glob's [ and ] must pair up"
    if depth:
        return "a glob's [ and ] must pair up"
    if pattern.count("{") != pattern.count("}"):
        return "a glob's { and } must pair up"
    return None


def _expand(pattern: str) -> list[str]:
    """{a,b} alternatives, which fnmatch does not know."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    return [p for option in match.group(1).split(",")
            for p in _expand(pattern[:match.start()] + option + pattern[match.end():])]


def _matches(path: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    return any(fnmatch.fnmatchcase(path, p) or fnmatch.fnmatchcase(path, p.replace("**/", ""))
               for p in _expand(pattern))


def load_index(root: Path) -> dict:
    return json.loads((Path(root) / INDEX).read_text())


def age(built_at: str, now: float | None = None) -> str:
    built = datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    days = int(((now or time.time()) - built.timestamp()) // 86400)
    return "today" if days < 1 else f"{days} day{'s' if days != 1 else ''} ago"


def run(ctx, query, path_glob=None, max_results=None, response_format=None):
    from tools import ToolError

    pattern = None if path_glob in (None, "") else str(path_glob)
    if pattern is not None:
        why = glob_problem(pattern)
        if why is not None:
            raise ToolError(
                f"path_glob {pattern!r} is not a valid path glob: {why}. A valid glob looks like "
                "src/ingest/*.ts or src/**/*.tsx, and null searches every indexed file. path_glob filters "
                "paths and is not a query language: put the text you are looking for in query instead. "
                "This will not succeed until path_glob is a glob or null.")

    fmt = DEFAULT_FORMAT if response_format in (None, "") else str(response_format)
    limit = DEFAULT_RESULTS if max_results in (None, "") else int(max_results)
    limit = max(1, min(limit, HARD_CAP))
    needle = str(query).strip().lower()

    index = load_index(Path(ctx.repo_root).parent.parent)
    files = index["files"]
    hits = [{"path": rel, "line": number, "text": line.strip()[:EXCERPT[fmt]]}
            for rel, lines in sorted(files.items()) if _matches(rel, pattern)
            for number, line in enumerate(lines, 1) if needle in line.lower()]
    shown = hits[:limit]
    return json.dumps({
        "query": str(query),
        "query_valid": True,
        "path_glob": pattern,
        "response_format": fmt,
        "count": len(hits),
        "returned": len(shown),
        "truncated": len(hits) > len(shown),
        "index_age": age(index["built_at"]),
        "results": shown,
    }, ensure_ascii=False)
