from . import payments
from .config import Settings
from .db import ConnectionPool


class OrderNotFound(Exception):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    charge_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

COLUMNS = "id, customer_id, amount_cents, charge_id, created_at"


def init_schema(pool: ConnectionPool) -> None:
    conn = pool.acquire()
    try:
        conn.execute(SCHEMA)
        conn.commit()
    finally:
        pool.release(conn)


def get_order(pool: ConnectionPool, order_id: int) -> dict:
    conn = pool.acquire()
    try:
        row = conn.execute(f"SELECT {COLUMNS} FROM orders WHERE id = ?", (order_id,)).fetchone()
    finally:
        pool.release(conn)
    if row is None:
        raise OrderNotFound(order_id)
    return dict(row)


def list_orders(pool: ConnectionPool, limit: int = 50) -> list[dict]:
    conn = pool.acquire()
    try:
        rows = conn.execute(f"SELECT {COLUMNS} FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        pool.release(conn)
    return [dict(r) for r in rows]


def create_order(pool: ConnectionPool, settings: Settings, customer_id: int, amount_cents: int) -> dict:
    charge_id = payments.charge(settings, customer_id, amount_cents)
    conn = pool.acquire()
    try:
        cur = conn.execute(
            "INSERT INTO orders (customer_id, amount_cents, charge_id) VALUES (?, ?, ?)",
            (customer_id, amount_cents, charge_id),
        )
        conn.commit()
        order_id = cur.lastrowid
    finally:
        pool.release(conn)
    return get_order(pool, order_id)
