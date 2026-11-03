"""s02: the env var that holds the gateway URL is renamed in code but not in the deploy env file."""

from datetime import timedelta

from incident_lab.logsim import Req, Resp, Sim

from .base import HEALTHY_PAYMENTS_URL, Mutation, Scenario, Timeline

FALLBACK_URL = "http://localhost:9000"

OLD = 'payments_url=env.get("PAYMENTS_URL", "http://localhost:9000"),'
NEW = 'payments_url=env.get("PAYMENTS_BASE_URL", "http://localhost:9000"),'


def simulate(sim: Sim, tl: Timeline) -> None:
    sim.startup(tl.start, tl.prev_version, HEALTHY_PAYMENTS_URL)
    sim.startup(tl.deploy, tl.version, FALLBACK_URL)

    def handler(req: Req, s: Sim) -> Resp:
        if req.ts < tl.deploy or req.kind != "create":
            return s.normal(req)

        elapsed_ms = s.rng.randint(1, 4)
        duration_ms = elapsed_ms + s.rng.randint(3, 8)
        s.mark_incident(req.ts + timedelta(milliseconds=duration_ms))
        msg = (
            f"charge failed customer_id={req.customer_id} url={FALLBACK_URL}/charge "
            f"error=ConnectError duration_ms={elapsed_ms}"
        )
        return Resp(502, duration_ms, s.idle_pool(), [("ERROR", "app.payments", msg, elapsed_ms)])

    sim.run(handler)


SCENARIO = Scenario(
    id="s02",
    title="Payment gateway URL silently falls back to localhost",
    category="bad_config",
    root_cause_file="app/config.py",
    culprit_subject="Config: use PAYMENTS_BASE_URL for the gateway address",
    culprit_body="Matches the naming used by the other services.",
    culprit_author=("Dmitri Volkov", "dmitri.volkov@example.com"),
    mutations=(Mutation("app/config.py", OLD, NEW),),
    after_deploy=timedelta(hours=1),
    alert_title="orders-api HTTP 5xx rate above 5% for 5m",
    simulate=simulate,
    summary=(
        "The code now reads PAYMENTS_BASE_URL but deploy/app.env still defines PAYMENTS_URL, so after the "
        "deploy the service silently falls back to the default http://localhost:9000 and every charge fails "
        "with ConnectError; POST /orders returns 502 while read endpoints stay healthy."
    ),
    key_signatures=("payments_url=http://localhost:9000", "ConnectError", "PAYMENTS_BASE_URL", "PAYMENTS_URL"),
    contributing_factors=("Silent default for the gateway URL instead of failing at startup",),
)
