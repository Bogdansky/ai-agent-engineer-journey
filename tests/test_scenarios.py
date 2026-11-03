"""Scenario integrity: every workspace must contain a real, reproducible fault with a clean ground truth."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from incident_lab.scenarios import REGISTRY
from incident_lab.workspace import build_workspace

LINE_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z (INFO|WARNING|ERROR)\s+(app\.\w+) .+$")
MESSAGE_STEMS = (
    "charge ok",
    "charge declined",
    "charge failed",
    "startup complete",
    "PoolExhausted: no free connection after",
)

# Python snippets that run inside the *workspace* code and print an observable symptom.
PROBES = {
    "s01": (
        """
import tempfile
from fastapi.testclient import TestClient
from app.config import load_settings
from app.main import create_app

d = tempfile.mkdtemp()
settings = load_settings({"DATABASE_PATH": d + "/o.db", "DB_POOL_SIZE": "3", "DB_ACQUIRE_TIMEOUT_S": "0.05"})
with TestClient(create_app(settings)) as client:
    print([client.get("/orders/999").status_code for _ in range(5)])
""",
        "[404, 404, 404, 503, 503]",
    ),
    "s02": (
        """
from app.config import load_settings
print(load_settings({"PAYMENTS_URL": "https://payments.internal:8443"}).payments_url)
""",
        "http://localhost:9000",
    ),
    "s03": (
        """
import httpx
from app import payments
from app.config import load_settings

seen = {}
def post(url, json, timeout):
    seen["timeout"] = timeout
    return httpx.Response(200, json={"charge_id": "c"}, request=httpx.Request("POST", url))

payments.httpx.post = post
payments.charge(load_settings({}), 1, 100)
print(seen["timeout"])
""",
        "None",
    ),
}


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("build")
    return {sid: build_workspace(sid, out) for sid in REGISTRY}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("sid", list(REGISTRY))
def test_fault_is_real_in_workspace_code(built, sid):
    code, expected = PROBES[sid]
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=built[sid].repo, check=True, capture_output=True, text=True
    )
    # the service itself logs to stdout, so the probe result is the last line
    assert result.stdout.strip().splitlines()[-1] == expected


@pytest.mark.parametrize("sid", list(REGISTRY))
def test_git_history_points_at_culprit(built, sid):
    scenario, ws = REGISTRY[sid], built[sid]
    assert git(ws.repo, "rev-parse", "HEAD") == ws.culprit_commit
    assert git(ws.repo, "log", "-1", "--format=%s") == scenario.culprit_subject
    assert git(ws.repo, "log", "-1", "--format=%H", "--", scenario.root_cause_file) == ws.culprit_commit
    assert len(git(ws.repo, "log", "--format=%h").splitlines()) == 4
    blamed = git(ws.repo, "blame", "--line-porcelain", scenario.root_cause_file)
    assert ws.culprit_commit in blamed


@pytest.mark.parametrize("sid", list(REGISTRY))
def test_logs_are_well_formed_and_match_the_code(built, sid):
    ws = built[sid]
    lines = ws.log_file.read_text().splitlines()
    assert len(lines) > 1000
    assert all(LINE_RE.match(line) for line in lines), "every line must follow the app log format"
    stamps = [line[:24] for line in lines]
    assert stamps == sorted(stamps), "log must be chronological"

    source = "\n".join(p.read_text() for p in ws.repo.rglob("*.py"))
    for logger in {LINE_RE.match(line).group(2) for line in lines}:
        assert f'getLogger("{logger}")' in source, f"no code writes to {logger}"
    for stem in MESSAGE_STEMS:
        if any(stem in line for line in lines):
            assert stem in source, f"log message '{stem}' not produced by any code"


@pytest.mark.parametrize("sid", list(REGISTRY))
def test_deploy_marker_matches_git_and_alert_follows_it(built, sid):
    ws = built[sid]
    startups = [line for line in ws.log_file.read_text().splitlines() if "startup complete" in line]
    assert len(startups) == 2
    assert f"version={ws.culprit_commit[:7]}" in startups[1]
    truth = json.loads(ws.truth_file.read_text())
    alert = ws.alert_file.read_text()
    assert f"fired_at: {truth['fired_at']}" in alert
    assert truth["fired_at"] > truth["deploy_ts"]


@pytest.mark.parametrize("sid", list(REGISTRY))
def test_ground_truth_does_not_leak_into_workspace(built, sid):
    scenario, ws = REGISTRY[sid], built[sid]
    assert not ws.truth_file.is_relative_to(ws.root)
    secrets = [scenario.category, scenario.summary[:40], "ground_truth", "key_signatures", "root_cause"]
    for path in ws.root.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(ws.root).parts:
            text = path.read_text(errors="ignore")
            for secret in secrets:
                assert secret not in text, f"{secret!r} leaked into {path.relative_to(ws.root)}"
    history = git(ws.repo, "log", "--format=%an %ae %s %b")
    assert scenario.category not in history


def test_builds_are_deterministic(built, tmp_path):
    again = build_workspace("s01", tmp_path)
    assert again.culprit_commit == built["s01"].culprit_commit
    assert again.log_file.read_text() == built["s01"].log_file.read_text()
    assert again.alert_file.read_text() == built["s01"].alert_file.read_text()
