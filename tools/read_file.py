"""code_read_file: return one file of the private codebase.

Its definition is read_file.json. It reads the same files code_search searches:
everything under fixtures/repo except config/. A path is relative to fixtures/repo,
as a search result prints it.

Assignment 3, Part B, task 3. It never asks the file system whether the path
exists. On a case-insensitive disk, which is macOS's default, src/report.ts opens
src/Report.ts and reports success, so a wrong path comes back as the wrong file.
Instead the path is matched against the repository's own list of paths, character
for character, and a miss returns an error naming every near match.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

CAPS = {"concise": 4_000, "detailed": 20_000}
DEFAULT_FORMAT = "concise"
MAX_NEAR_MISSES = 6


def repository_paths(repo_root) -> list[str]:
    """Every readable path in the repository, relative, with forward slashes."""
    from tools.search_repo import EXCLUDED_DIRS

    root = Path(repo_root)
    out = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and not (set(rel.parts) & EXCLUDED_DIRS):
            out.append(rel.as_posix())
    return out


def _stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _folder(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def near_misses(wanted: str, known: list[str]) -> list[str]:
    """Paths a reader could mistake for `wanted`: the same path in another case, and,
    in the same directory, a name that is the same but for its extension or a plural.
    These are what a case-insensitive disk would have opened instead."""
    low = wanted.lower()
    want_stem, want_folder = _stem(low), _folder(low)
    found = []
    for p in known:
        if p == wanted:
            continue
        low_p = p.lower()
        stem_p = _stem(low_p)
        if low_p == low or (_folder(low_p) == want_folder
                            and (stem_p.startswith(want_stem) or want_stem.startswith(stem_p))):
            found.append(p)
    rest = [p for p in known if p != wanted and p not in found]
    found += difflib.get_close_matches(wanted, rest, n=2, cutoff=0.9)
    return found[:MAX_NEAR_MISSES]


def run(ctx, path, response_format=None, line_range=None):
    from harness.formats import normalize_path
    from tools import ToolError

    rel = normalize_path(str(path))
    if rel in (None, "none"):
        raise ToolError("path is empty: pass one file path relative to the repository root, such as "
                        "src/util/money.ts, exactly as a search result printed it.")
    known = repository_paths(ctx.repo_root)
    if rel not in known:
        close = near_misses(rel, known)
        hint = (" The repository has these near matches, and they are different files: "
                + ", ".join(close) + ".") if close else ""
        raise ToolError(
            f"no file at {rel} in the repository.{hint} Paths are matched exactly, case included, relative "
            "to the repository root, and this tool compares the path with the repository's own file list "
            "rather than asking the file system, so a wrong case fails here instead of opening the other "
            "file. Copy a path from a search result. This will not succeed with any other spelling of this path.")

    fmt = DEFAULT_FORMAT if response_format in (None, "") else str(response_format)
    cap = CAPS[fmt]
    try:
        text = (Path(ctx.repo_root) / rel).read_text()
    except UnicodeDecodeError:
        raise ToolError(f"{rel} is not a text file, so it cannot be read as source. This will not succeed "
                        "with any arguments; pick a text file from a search result instead.")
    lines = text.splitlines()
    first, last = (1, len(lines))
    kind = (line_range or {}).get("kind") or "whole_file"
    if kind == "range" and lines:
        start = (line_range or {}).get("start") or 1
        end = (line_range or {}).get("end") or len(lines)
        first, last = max(1, int(start)), min(len(lines), int(end))
        if first > last:
            raise ToolError(f"line_range asks for lines {start} to {end}, which is empty, and {rel} has "
                            f"{len(lines)} lines. start must be at most end, both counting from 1 and both "
                            "inside the file. This will not succeed until they are.")
        text = "\n".join(lines[first - 1:last])
    body = text[:cap]
    return json.dumps({
        "path": rel,
        "found": True,
        "lines": len(lines),
        "first_line": first if lines else 0,
        "last_line": last if lines else 0,
        "chars_returned": len(body),
        "truncated": len(text) > len(body),
        "response_format": fmt,
        "text": body,
    }, ensure_ascii=False)
