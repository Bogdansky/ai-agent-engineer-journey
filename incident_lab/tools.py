"""Read-only investigation tools over one incident workspace.

These are plain functions on purpose: the MCP server exposes them today, and the hand-written
agent loop can call the very same code directly. Every method returns text for the model to read
and raises ``ToolError`` with a message the model can act on.

Safety model: nothing here writes, paths cannot leave ``repo/``, ``.git`` internals are not
readable as files, git runs without a shell and without any user/system configuration, and every
result is size-capped so one call cannot flood the context window.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

TOOL_NAMES = ("get_alert", "list_files", "read_file", "search_logs", "git_log", "git_show", "git_blame")

_COMMIT_RE = re.compile(r"[0-9a-fA-F]{4,40}|HEAD(~\d{1,3})?")
_LOG_LINE_RE = re.compile(r"^(\S+) (\w+)\s+(\S+) ")
_HIDDEN_DIRS = {".git", "__pycache__"}


class ToolError(Exception):
    """A problem the model can fix by changing its request."""


class WorkspaceTools:
    def __init__(
        self,
        workspace: Path,
        *,
        max_output_chars: int = 8000,
        max_read_lines: int = 300,
        max_search_results: int = 50,
    ) -> None:
        self.root = Path(workspace).resolve()
        self.repo = self.root / "repo"
        self.log_file = self.root / "logs" / "app.log"
        self.alert_file = self.root / "alert.txt"
        self.max_output_chars = max_output_chars
        self.max_read_lines = max_read_lines
        self.max_search_results = max_search_results
        if not self.repo.is_dir() or not self.log_file.is_file():
            raise ValueError(f"{self.root} is not a built incident workspace (expected repo/ and logs/app.log)")

    # ------------------------------------------------------------------ helpers

    def _clip(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        dropped = len(text) - self.max_output_chars
        return text[: self.max_output_chars] + f"\n... [truncated {dropped} characters; narrow the request]"

    def _resolve(self, rel: str, *, must_be: str | None = None) -> Path:
        if not rel or "\x00" in rel:
            raise ToolError("path must be a non-empty relative path inside the repository")
        candidate = Path(rel)
        if candidate.is_absolute():
            raise ToolError("absolute paths are not allowed; use a path relative to the repository root")
        target = (self.repo / candidate).resolve()
        if not target.is_relative_to(self.repo):
            raise ToolError("path escapes the repository")
        if _HIDDEN_DIRS & set(target.relative_to(self.repo).parts):
            raise ToolError("git internals are not readable as files; use git_log, git_show or git_blame")
        if not target.exists():
            raise ToolError(f"no such path in the repository: {rel}")
        if must_be == "file" and not target.is_file():
            raise ToolError(f"not a file: {rel}")
        if must_be == "dir" and not target.is_dir():
            raise ToolError(f"not a directory: {rel}")
        return target

    def _git(self, *args: str) -> str:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C.UTF-8",
        }
        try:
            result = subprocess.run(
                ["git", "--no-pager", "-c", "safe.directory=*", "-C", str(self.repo), *args],
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            raise ToolError("git command timed out") from None
        if result.returncode != 0:
            first = (result.stderr.strip().splitlines() or ["git failed"])[0]
            raise ToolError(first)
        return result.stdout

    @staticmethod
    def _line_range(start_line: int, end_line: int | None) -> tuple[int, int | None]:
        if start_line < 1:
            raise ToolError("start_line must be >= 1")
        if end_line is not None and end_line < start_line:
            raise ToolError("end_line must be >= start_line")
        return start_line, end_line

    # -------------------------------------------------------------------- tools

    def get_alert(self) -> str:
        """Return the alert that triggered this investigation (what fired, for which service, and when)."""
        return self.alert_file.read_text()

    def list_files(self, path: str = ".", recursive: bool = False) -> str:
        """List files and directories in the service repository. `path` is relative to the repository root."""
        target = self.repo if path in (".", "") else self._resolve(path, must_be="dir")
        entries: list[str] = []
        if recursive:
            for p in sorted(target.rglob("*")):
                rel = p.relative_to(self.repo)
                if _HIDDEN_DIRS & set(rel.parts):
                    continue
                entries.append(f"{rel}/" if p.is_dir() else str(rel))
                if len(entries) >= 200:
                    entries.append("... [more entries omitted]")
                    break
        else:
            for p in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
                if p.name in _HIDDEN_DIRS:
                    continue
                entries.append(f"{p.name}/" if p.is_dir() else p.name)
        return self._clip("\n".join(entries) or "(empty directory)")

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Read a text file from the service repository with line numbers. Returns at most 300 lines per call;
        use start_line/end_line to page through longer files."""
        start, end = self._line_range(start_line, end_line)
        target = self._resolve(path, must_be="file")
        if target.stat().st_size > 1_000_000:
            raise ToolError("file is larger than 1 MB; this tool is meant for source files")
        try:
            lines = target.read_text().splitlines()
        except UnicodeDecodeError:
            raise ToolError("file is not valid UTF-8 text") from None
        total = len(lines)
        if total and start > total:
            raise ToolError(f"start_line {start} is past the end of the file ({total} lines)")
        last = min(end if end is not None else total, total, start + self.max_read_lines - 1)
        body = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, last + 1))
        footer = f"\n[showing lines {start}-{last} of {total}]"
        return self._clip(body + footer)

    def search_logs(
        self,
        pattern: str,
        start: str | None = None,
        end: str | None = None,
        level: str | None = None,
        limit: int = 30,
        ignore_case: bool = False,
    ) -> str:
        """Search the service log with a regular expression. Optional filters: `start`/`end` are inclusive ISO
        timestamp prefixes (e.g. '2026-10-20T12:05'), `level` is INFO, WARNING or ERROR. Results are the
        first `limit` matches in chronological order, with line numbers and a total match count."""
        if len(pattern) > 200:
            raise ToolError("pattern is too long (max 200 characters)")
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}") from None
        wanted_level = level.upper() if level else None
        if wanted_level and wanted_level not in {"INFO", "WARNING", "ERROR"}:
            raise ToolError("level must be INFO, WARNING or ERROR")
        limit = max(1, min(limit, self.max_search_results))

        lines = self.log_file.read_text().splitlines()
        matches: list[str] = []
        total_matches = 0
        for number, line in enumerate(lines, start=1):
            if start and line[: len(start)] < start:
                continue
            if end and line[: len(end)] > end:
                continue
            if wanted_level:
                parsed = _LOG_LINE_RE.match(line)
                if not parsed or parsed.group(2) != wanted_level:
                    continue
            if not regex.search(line):
                continue
            total_matches += 1
            if len(matches) < limit:
                matches.append(f"L{number}: {line}")

        header = f"matched {total_matches} of {len(lines)} lines"
        if total_matches > len(matches):
            header += f" (showing first {len(matches)}; narrow with start/end/level or a tighter pattern)"
        return self._clip(header + ("\n" + "\n".join(matches) if matches else ""))

    def git_log(self, path: str | None = None, limit: int = 20) -> str:
        """Show commit history, newest first: short hash, date, author, subject. Pass `path` to see only the
        commits that touched that file."""
        limit = max(1, min(limit, 50))
        args = ["log", f"-n{limit}", "--date=iso-strict", "--format=%h %ad %an | %s"]
        if path:
            args += ["--", str(self._resolve(path).relative_to(self.repo))]
        return self._clip(self._git(*args).strip() or "(no commits)")

    def git_show(self, commit: str, path: str | None = None) -> str:
        """Show one commit: message, files changed and the full diff. `commit` is a hash prefix or HEAD~N.
        Pass `path` to limit the diff to one file."""
        if not _COMMIT_RE.fullmatch(commit):
            raise ToolError("commit must be a hex hash (4-40 chars), HEAD or HEAD~N")
        args = ["show", "--no-ext-diff", "--no-textconv", "--date=iso-strict", "--stat", "--patch", commit]
        if path:
            args += ["--", str(self._resolve(path).relative_to(self.repo))]
        return self._clip(self._git(*args).strip())

    def git_blame(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Show which commit and author last changed each line of a file. Use start_line/end_line to focus on
        the lines of interest."""
        start, end = self._line_range(start_line, end_line)
        target = self._resolve(path, must_be="file")
        total = len(target.read_text().splitlines())
        if start > total:
            raise ToolError(f"start_line {start} is past the end of the file ({total} lines)")
        last = min(end if end is not None else total, total)
        rel = str(target.relative_to(self.repo))
        return self._clip(self._git("blame", "--date=short", "-L", f"{start},{last}", "--", rel).rstrip())
