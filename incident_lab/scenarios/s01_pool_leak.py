"""s01: a refactor drops try/finally, so a 404 lookup leaks a pooled DB connection."""

from datetime import timedelta

from incident_lab.logsim import Req, Resp, Sim

from .base import HEALTHY_PAYMENTS_URL, Mutation, Scenario, Timeline

OLD = '''    conn = pool.acquire()
    try:
        row = conn.execute(f"SELECT {COLUMNS} FROM orders WHERE id = ?", (order_id,)).fetchone()
    finally:
        pool.release(conn)
    if row is None:
        raise OrderNotFound(order_id)
    return dict(row)
'''

NEW = '''    conn = pool.acquire()
    row = conn.execute(f"SELECT {COLUMNS} FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise OrderNotFound(order_id)
    pool.release(conn)
    return dict(row)
'''


def simulate(sim: Sim, tl: Timeline) -> None:
    sim.startup(tl.start, tl.prev_version, HEALTHY_PAYMENTS_URL)
    sim.startup(tl.deploy, tl.version, HEALTHY_PAYMENTS_URL)
    size = sim.pool_size
    leaked = 0

    def handler(req: Req, s: Sim) -> Resp:
        nonlocal leaked
        if req.ts < tl.deploy or req.kind == "health":
            return s.normal(req)

        transient = s.rng.choice([0, 0, 0, 1, 1, 2])
        if leaked + transient >= size:
            duration_ms = 2000 + s.rng.randint(0, 30)
            s.mark_incident(req.ts + timedelta(milliseconds=duration_ms))
            msg = f"PoolExhausted: no free connection after 2.0s (size={size}, in_use={size})"
            return Resp(503, duration_ms, f"{size}/{size}", [("ERROR", "app.db", msg, 2000)])

        resp = s.normal(req)
        if req.kind == "get_missing":
            leaked += 1
        in_use = size if leaked >= size else min(leaked + transient, size - 1)
        resp.pool = f"{in_use}/{size}"
        return resp

    sim.run(handler)


SCENARIO = Scenario(
    id="s01",
    title="Connection pool leak on missing orders",
    category="connection_leak",
    root_cause_file="app/orders.py",
    culprit_subject="Orders: simplify get_order lookup",
    culprit_body="Single statement, no need for the extra nesting.",
    culprit_author=("Mei Tan", "mei.tan@example.com"),
    mutations=(Mutation("app/orders.py", OLD, NEW),),
    after_deploy=timedelta(hours=1),
    alert_title="orders-api HTTP 5xx rate above 5% for 5m",
    simulate=simulate,
    summary=(
        "get_order no longer releases its pooled connection when the order does not exist (the try/finally "
        "was removed). Every 404 lookup leaks one of the 10 pool connections; after enough misses the pool "
        "is exhausted and every database-backed endpoint returns 503."
    ),
    key_signatures=("PoolExhausted", "pool_in_use", "status=404"),
    contributing_factors=("Fixed pool size of 10 with a 2s acquire timeout turns the leak into a full outage",),
)
