import os
import re
import subprocess

import pytest

from incident_lab.tools import ToolError, WorkspaceTools
from incident_lab.workspace import build_workspace


@pytest.fixture
def tools(built):
    return WorkspaceTools(built["s01"].root)


def test_rejects_directory_that_is_not_a_workspace(tmp_path):
    with pytest.raises(ValueError):
        WorkspaceTools(tmp_path)


def test_get_alert(tools):
    assert "[FIRING]" in tools.get_alert()


def test_list_files_hides_git_and_supports_recursion(tools):
    top = tools.list_files().splitlines()
    assert "app/" in top and "README.md" in top and ".git/" not in top
    assert "app/orders.py" in tools.list_files(recursive=True).splitlines()
    assert "orders.py" in tools.list_files("app").splitlines()
    with pytest.raises(ToolError):
        tools.list_files("nope")


def test_read_file_numbers_lines_and_pages(built):
    small = WorkspaceTools(built["s01"].root, max_read_lines=5)
    out = small.read_file("app/orders.py")
    assert "[showing lines 1-5 of" in out
    assert out.splitlines()[0].strip().startswith("1")
    window = small.read_file("app/orders.py", start_line=3, end_line=4)
    assert "[showing lines 3-4 of" in window
    with pytest.raises(ToolError):
        small.read_file("app/orders.py", start_line=10_000)
    with pytest.raises(ToolError):
        small.read_file("app/orders.py", start_line=5, end_line=2)


@pytest.mark.parametrize(
    "bad_path",
    ["../../etc/passwd", "/etc/passwd", ".git/config", "app/../../../etc/passwd", "app/orders.py\x00.txt", ""],
)
def test_paths_cannot_escape_the_repository(tools, bad_path):
    with pytest.raises(ToolError):
        tools.read_file(bad_path)
    with pytest.raises(ToolError):
        tools.git_blame(bad_path)


def test_symlinks_cannot_be_used_to_escape(tmp_path):
    ws = build_workspace("s01", tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    os.symlink(secret, ws.repo / "leak.txt")
    with pytest.raises(ToolError, match="escapes"):
        WorkspaceTools(ws.root).read_file("leak.txt")


def test_search_logs_filters_and_counts(tools):
    both = tools.search_logs("startup complete")
    assert both.startswith("matched 2 of ")
    assert tools.search_logs("startup complete", start="2026-10-20T11").startswith("matched 1 of ")
    assert tools.search_logs("startup complete", end="2026-10-20T11").startswith("matched 1 of ")

    errors = tools.search_logs("PoolExhausted", level="error", limit=2)
    assert "showing first 2" in errors and errors.count("\nL") == 2
    assert tools.search_logs("poolexhausted", ignore_case=True, level="ERROR").startswith("matched ")
    assert tools.search_logs("this string is not in the log").startswith("matched 0 of ")


def test_search_logs_rejects_bad_input(tools):
    with pytest.raises(ToolError, match="regular expression"):
        tools.search_logs("(")
    with pytest.raises(ToolError, match="level"):
        tools.search_logs("x", level="DEBUG")
    with pytest.raises(ToolError, match="too long"):
        tools.search_logs("a" * 201)


def test_search_limit_is_clamped(built):
    capped = WorkspaceTools(built["s01"].root, max_search_results=3)
    assert capped.search_logs("app.http", limit=1000).count("\nL") == 3


def test_git_log_and_show_lead_to_the_culprit(built, tools):
    culprit = built["s01"].culprit_commit
    assert len(tools.git_log().splitlines()) == 4
    assert tools.git_log().splitlines()[0].startswith(culprit[:7])
    assert tools.git_log("app/orders.py").splitlines()[0].startswith(culprit[:7])

    shown = tools.git_show(culprit[:7], path="app/orders.py")
    assert "Orders: simplify get_order lookup" in shown
    assert "-    try:" in shown and "+    pool.release(conn)" in shown
    assert "uvicorn" not in shown, "path filter must restrict the diff"
    assert "Bump uvicorn" in tools.git_show("HEAD~1"), "HEAD~N form must be accepted"


def test_git_show_rejects_option_injection(tools):
    for bad in ["--output=/tmp/pwned", "-p", "HEAD; rm -rf /", "zzzz", ""]:
        with pytest.raises(ToolError):
            tools.git_show(bad)
    with pytest.raises(ToolError):
        tools.git_show("deadbeef")


def test_git_blame_attributes_the_changed_line(built, tools):
    blame = tools.git_blame("app/orders.py")
    # the culprit's new line has a 4-space indent; the older ones inside finally: have 8
    hit = [line for line in blame.splitlines() if re.search(r"\) {5}pool\.release\(conn\)$", line)]
    assert hit and hit[0].startswith(built["s01"].culprit_commit[:7])
    assert len(tools.git_blame("app/orders.py", 1, 3).splitlines()) == 3


def test_output_is_clipped(built):
    tiny = WorkspaceTools(built["s01"].root, max_output_chars=100)
    assert "truncated" in tiny.read_file("app/main.py")
    assert "truncated" in tiny.git_show("HEAD")


def test_tools_never_modify_the_workspace(built, tools):
    repo = built["s01"].repo

    def state():
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
        return head, status, built["s01"].log_file.read_text()

    before = state()
    tools.get_alert()
    tools.list_files(recursive=True)
    tools.read_file("app/orders.py")
    tools.search_logs("status=503", limit=5)
    tools.git_log()
    tools.git_show("HEAD")
    tools.git_blame("app/orders.py")
    assert state() == before
    assert before[1] == "", "workspace repo must stay clean"
