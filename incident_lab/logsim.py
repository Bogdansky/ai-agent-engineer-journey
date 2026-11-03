"""Deterministic simulator of orders-api production logs.

Every line it emits matches a log call that exists in ``sandbox/app`` (same logger names,
same message formats), so the agent can always map a log line back to the code that wrote it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

UTC = timezone.utc


def fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


@dataclass
class Req:
    ts: datetime
    kind: str  # health | get_found | get_missing | create | list
    method: str
    path: str
    customer_id: int | None = None


@dataclass
class Resp:
    status: int
    duration_ms: int
    pool: str | None = None
    # (level, logger, message, offset in ms from request start)
    pre: list[tuple[str, str, str, int]] = field(default_factory=list)


Handler = Callable[[Req, "Sim"], Resp]


class Sim:
    def __init__(self, seed: int, start: datetime, end: datetime, pool_size: int = 10) -> None:
        self.rng = random.Random(seed)
        self.start = start
        self.end = end
        self.pool_size = pool_size
        self.incident_ts: datetime | None = None
        self._lines: list[tuple[datetime, int, str]] = []

    # --- output -------------------------------------------------------------

    def emit(self, ts: datetime, level: str, logger: str, msg: str) -> None:
        self._lines.append((ts, len(self._lines), f"{fmt_ts(ts)} {level:<7} {logger} {msg}"))

    def mark_incident(self, ts: datetime) -> None:
        if self.incident_ts is None or ts < self.incident_ts:
            self.incident_ts = ts

    def render(self) -> str:
        return "\n".join(line for _, _, line in sorted(self._lines)) + "\n"

    def startup(self, ts: datetime, version: str, payments_url: str) -> None:
        self.emit(
            ts,
            "INFO",
            "app.main",
            f"startup complete version={version} pool_size={self.pool_size} payments_url={payments_url}",
        )

    # --- traffic ------------------------------------------------------------

    def _traffic(self, mean_gap_s: float) -> list[Req]:
        rng = self.rng
        kinds = ["get_found", "get_missing", "create", "list"]
        weights = [0.60, 0.10, 0.20, 0.10]
        reqs: list[Req] = []

        t = self.start
        while True:
            t += timedelta(seconds=rng.expovariate(1.0 / mean_gap_s))
            if t >= self.end:
                break
            kind = rng.choices(kinds, weights)[0]
            if kind == "get_found":
                reqs.append(Req(t, kind, "GET", f"/orders/{rng.randint(1000, 9000)}"))
            elif kind == "get_missing":
                reqs.append(Req(t, kind, "GET", f"/orders/{rng.randint(900000, 999999)}"))
            elif kind == "create":
                reqs.append(Req(t, kind, "POST", "/orders", customer_id=rng.randint(1, 5000)))
            else:
                reqs.append(Req(t, kind, "GET", "/orders"))

        t = self.start
        while t < self.end:
            reqs.append(Req(t + timedelta(milliseconds=rng.randint(0, 2000)), "health", "GET", "/health"))
            t += timedelta(seconds=30)

        reqs.sort(key=lambda r: r.ts)
        return reqs

    def idle_pool(self) -> str:
        return f"{self.rng.choice([0, 0, 0, 1, 1, 2])}/{self.pool_size}"

    def normal(self, req: Req) -> Resp:
        """Healthy behaviour of the service."""
        rng = self.rng
        if req.kind == "health":
            return Resp(200, rng.randint(1, 3))
        if req.kind == "get_found":
            return Resp(200, rng.randint(4, 25), self.idle_pool())
        if req.kind == "get_missing":
            return Resp(404, rng.randint(3, 12), self.idle_pool())
        if req.kind == "list":
            return Resp(200, rng.randint(15, 60), self.idle_pool())
        charge_ms = rng.randint(120, 380)
        if rng.random() < 0.03:
            return Resp(
                402,
                charge_ms + rng.randint(8, 25),
                self.idle_pool(),
                [("WARNING", "app.payments", f"charge declined customer_id={req.customer_id} reason=insufficient_funds", charge_ms)],
            )
        return Resp(
            201,
            charge_ms + rng.randint(8, 25),
            self.idle_pool(),
            [("INFO", "app.payments", f"charge ok customer_id={req.customer_id} duration_ms={charge_ms}", charge_ms)],
        )

    def run(self, handler: Handler | None = None, mean_gap_s: float = 6.0) -> None:
        handler = handler or (lambda req, sim: sim.normal(req))
        for req in self._traffic(mean_gap_s):
            resp = handler(req, self)
            for level, logger, msg, offset_ms in resp.pre:
                self.emit(req.ts + timedelta(milliseconds=offset_ms), level, logger, msg)
            extra = f" pool_in_use={resp.pool}" if resp.pool else ""
            self.emit(
                req.ts + timedelta(milliseconds=resp.duration_ms),
                "INFO",
                "app.http",
                f"method={req.method} path={req.path} status={resp.status} duration_ms={resp.duration_ms}{extra}",
            )
