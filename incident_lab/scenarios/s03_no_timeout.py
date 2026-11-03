"""s03: the payments client timeout is disabled; a slow gateway later saturates the worker threads."""

from datetime import timedelta

from incident_lab.logsim import Req, Resp, Sim

from .base import HEALTHY_PAYMENTS_URL, Mutation, Scenario, Timeline

OLD = "            timeout=settings.payments_timeout_s,\n"
NEW = "            timeout=None,\n"

HANG_S = 300  # the gateway side closes idle connections after 5 minutes
MAX_THREADS = 12


def simulate(sim: Sim, tl: Timeline) -> None:
    sim.startup(tl.start, tl.prev_version, HEALTHY_PAYMENTS_URL)
    sim.startup(tl.deploy, tl.version, HEALTHY_PAYMENTS_URL)
    degraded_from = tl.deploy + timedelta(hours=1)
    stuck_until: list = []  # when each hung charge call will finally be cut off

    def handler(req: Req, s: Sim) -> Resp:
        nonlocal stuck_until
        if req.ts < degraded_from or req.kind == "health":
            return s.normal(req)

        stuck_until = [t for t in stuck_until if t > req.ts]
        wait_ms = 0
        if len(stuck_until) >= MAX_THREADS:
            # every worker thread is blocked in a charge call; the request first waits for a free thread
            wait_ms = s.rng.randint(8000, 30000)
            s.mark_incident(req.ts + timedelta(milliseconds=wait_ms))

        if req.kind == "create":
            # the gateway no longer answers, so this call hangs too and holds its thread for HANG_S
            stuck_until.append(req.ts + timedelta(milliseconds=wait_ms, seconds=HANG_S))
            hung_ms = HANG_S * 1000 + s.rng.randint(0, 40)
            total_ms = wait_ms + hung_ms + s.rng.randint(1, 6)
            s.mark_incident(req.ts + timedelta(milliseconds=total_ms))
            msg = (
                f"charge failed customer_id={req.customer_id} url={HEALTHY_PAYMENTS_URL}/charge "
                f"error=RemoteProtocolError duration_ms={hung_ms}"
            )
            return Resp(502, total_ms, s.idle_pool(), [("ERROR", "app.payments", msg, wait_ms + hung_ms)])

        resp = s.normal(req)
        resp.duration_ms += wait_ms
        return resp

    sim.run(handler)


SCENARIO = Scenario(
    id="s03",
    title="Slow payment gateway saturates workers (no client timeout)",
    category="missing_timeout",
    root_cause_file="app/payments.py",
    culprit_subject="Payments: stop client timeouts from failing charges",
    culprit_body="Slow gateway responses were being cut off at 3s and surfacing as errors. Let the request run to completion.",
    culprit_author=("Alice Novak", "alice.novak@example.com"),
    mutations=(Mutation("app/payments.py", OLD, NEW),),
    after_deploy=timedelta(hours=2),
    alert_title="orders-api p95 latency above 10s for 5m",
    simulate=simulate,
    summary=(
        "The deploy set the payments HTTP client timeout to None (PAYMENTS_TIMEOUT_S is now ignored). An hour "
        "later the gateway stops answering; each POST /orders then blocks a worker thread for about 5 minutes "
        "until the gateway drops the connection. With only 12 worker threads, all of them end up blocked and "
        "every other endpoint queues behind them, so latency explodes although /health stays green."
    ),
    key_signatures=("RemoteProtocolError", "duration_ms=300", "timeout=None", "PAYMENTS_TIMEOUT_S"),
    contributing_factors=(
        "Payments gateway degradation is the trigger, not the root cause",
        "Small worker thread pool (MAX_THREADS=12) shared by all endpoints",
        "/health is async and does not reflect worker saturation",
    ),
)
