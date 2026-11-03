from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from incident_lab.logsim import UTC, Sim

START = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
DEPLOY = START + timedelta(hours=2)
HEALTHY_PAYMENTS_URL = "https://payments.internal:8443"


@dataclass(frozen=True)
class Timeline:
    start: datetime
    deploy: datetime
    end: datetime
    prev_version: str  # short sha running before the culprit deploy
    version: str  # short sha of the culprit deploy


@dataclass(frozen=True)
class Mutation:
    path: str
    old: str
    new: str

    def apply(self, repo: Path) -> None:
        target = repo / self.path
        text = target.read_text()
        if text.count(self.old) != 1:
            raise ValueError(f"mutation anchor must match exactly once in {self.path}, got {text.count(self.old)}")
        target.write_text(text.replace(self.old, self.new))


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    category: str
    root_cause_file: str
    culprit_subject: str
    culprit_body: str
    culprit_author: tuple[str, str]
    mutations: tuple[Mutation, ...]
    after_deploy: timedelta
    alert_title: str
    simulate: Callable[[Sim, Timeline], None]
    summary: str  # reference explanation; ground truth only, never written into the workspace
    key_signatures: tuple[str, ...]
    contributing_factors: tuple[str, ...] = field(default_factory=tuple)
