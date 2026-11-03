"""Build a self-contained incident workspace for one scenario.

    python -m incident_lab.workspace s01            # one scenario
    python -m incident_lab.workspace all            # every scenario

Layout (what the agent may see lives under ``workspaces/<id>/``; ground truth never does):

    build/
      workspaces/<id>/
        alert.txt
        logs/app.log
        repo/                 git repository with the service at the culprit deploy
      truth/<id>.json         ground truth for graders only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from incident_lab.logsim import UTC, Sim
from incident_lab.scenarios import REGISTRY, Scenario
from incident_lab.scenarios.base import DEPLOY, START, Timeline

SANDBOX_DIR = Path(__file__).resolve().parent.parent / "sandbox"
SEED_BASE = 20261020

ALICE = ("Alice Novak", "alice.novak@example.com")
DMITRI = ("Dmitri Volkov", "dmitri.volkov@example.com")
MEI = ("Mei Tan", "mei.tan@example.com")


@dataclass(frozen=True)
class BuiltWorkspace:
    scenario_id: str
    root: Path
    repo: Path
    log_file: Path
    alert_file: Path
    truth_file: Path
    culprit_commit: str


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env={**os.environ, **(env or {})},
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, subject: str, body: str, author: tuple[str, str], when: datetime) -> str:
    name, email = author
    stamp = when.isoformat()
    env = {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_COMMITTER_DATE": stamp,
    }
    _git(repo, "add", "-A")
    message = subject if not body else f"{subject}\n\n{body}"
    _git(repo, "commit", "-q", "-m", message, env=env)
    return _git(repo, "rev-parse", "HEAD")


def _build_repo(scenario: Scenario, repo: Path) -> tuple[str, str]:
    """Create the git history. Returns (previous deploy sha, culprit sha)."""
    shutil.copytree(SANDBOX_DIR, repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db"))
    _git(repo, "init", "-q", "-b", "main")

    _commit(repo, "Initial orders-api service", "", ALICE, DEPLOY - timedelta(days=21))

    readme = repo / "README.md"
    readme.write_text(
        readme.read_text()
        + "\n## Runbook\n\n- Restart: `systemctl restart orders-api`\n- Logs: `logs/app.log`\n"
    )
    _commit(repo, "docs: add runbook notes to README", "", DMITRI, DEPLOY - timedelta(days=14))

    requirements = repo / "requirements.txt"
    requirements.write_text(requirements.read_text().replace("uvicorn==0.33.0", "uvicorn==0.34.0"))
    prev = _commit(repo, "Bump uvicorn to 0.34.0", "", MEI, DEPLOY - timedelta(days=5))

    for mutation in scenario.mutations:
        mutation.apply(repo)
    culprit = _commit(
        repo, scenario.culprit_subject, scenario.culprit_body, scenario.culprit_author, DEPLOY - timedelta(minutes=25)
    )
    return prev, culprit


def build_workspace(scenario_id: str, out_root: Path = Path("build")) -> BuiltWorkspace:
    scenario = REGISTRY[scenario_id]
    root = out_root / "workspaces" / scenario_id
    truth_file = out_root / "truth" / f"{scenario_id}.json"
    if root.exists():
        shutil.rmtree(root)
    (root / "logs").mkdir(parents=True)
    truth_file.parent.mkdir(parents=True, exist_ok=True)

    repo = root / "repo"
    prev_sha, culprit_sha = _build_repo(scenario, repo)

    end = DEPLOY + scenario.after_deploy
    timeline = Timeline(START, DEPLOY, end, prev_sha[:7], culprit_sha[:7])
    seed = SEED_BASE + int(scenario_id.lstrip("s"))
    sim = Sim(seed, START, end)
    scenario.simulate(sim, timeline)
    if sim.incident_ts is None:
        raise RuntimeError(f"{scenario_id}: simulation produced no incident")

    log_file = root / "logs" / "app.log"
    log_file.write_text(sim.render())

    fired_at = sim.incident_ts.replace(second=0, microsecond=0) + timedelta(minutes=5)
    alert_file = root / "alert.txt"
    alert_file.write_text(
        f"[FIRING] {scenario.alert_title}\n"
        f"service: orders-api\n"
        f"env: production\n"
        f"fired_at: {fired_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
    )

    truth_file.write_text(
        json.dumps(
            {
                "id": scenario.id,
                "title": scenario.title,
                "category": scenario.category,
                "root_cause_file": scenario.root_cause_file,
                "culprit_commit": culprit_sha,
                "culprit_subject": scenario.culprit_subject,
                "summary": scenario.summary,
                "key_signatures": list(scenario.key_signatures),
                "contributing_factors": list(scenario.contributing_factors),
                "deploy_ts": DEPLOY.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fired_at": fired_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    return BuiltWorkspace(scenario_id, root, repo, log_file, alert_file, truth_file, culprit_sha)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build incident workspaces")
    parser.add_argument("scenario", help="scenario id (e.g. s01) or 'all'")
    parser.add_argument("--out", default="build", type=Path)
    args = parser.parse_args()

    ids = list(REGISTRY) if args.scenario == "all" else [args.scenario]
    for scenario_id in ids:
        built = build_workspace(scenario_id, args.out)
        print(f"{scenario_id}: workspace={built.root} culprit={built.culprit_commit[:7]}")


if __name__ == "__main__":
    main()
