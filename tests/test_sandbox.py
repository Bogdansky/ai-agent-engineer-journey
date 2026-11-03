"""Sanity checks for the healthy (baseline) sandbox service."""

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

SANDBOX = Path(__file__).resolve().parent.parent / "sandbox"
sys.path.insert(0, str(SANDBOX))

from app import payments  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.main import create_app  # noqa: E402


def make_client(tmp_path, **env):
    settings = load_settings(
        {"DATABASE_PATH": str(tmp_path / "orders.db"), "DB_POOL_SIZE": "3", "DB_ACQUIRE_TIMEOUT_S": "0.05", **env}
    )
    return TestClient(create_app(settings))


@pytest.fixture
def fake_gateway(monkeypatch):
    def post(url, json, timeout):
        return httpx.Response(200, json={"charge_id": "ch_1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(payments.httpx, "post", post)


def test_health(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_create_and_get_order(tmp_path, fake_gateway):
    with make_client(tmp_path) as client:
        created = client.post("/orders", json={"customer_id": 7, "amount_cents": 1999})
        assert created.status_code == 201
        order_id = created.json()["id"]
        assert client.get(f"/orders/{order_id}").json()["amount_cents"] == 1999
        assert len(client.get("/orders").json()) == 1


def test_missing_order_is_404_and_releases_connection(tmp_path):
    with make_client(tmp_path) as client:
        codes = [client.get("/orders/999").status_code for _ in range(10)]
        assert codes == [404] * 10
        assert client.app.state.pool.in_use == 0


def test_payment_gateway_down_is_502(tmp_path, monkeypatch):
    def post(url, json, timeout):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(payments.httpx, "post", post)
    with make_client(tmp_path) as client:
        resp = client.post("/orders", json={"customer_id": 1, "amount_cents": 500})
        assert resp.status_code == 502


def test_declined_payment_is_402(tmp_path, monkeypatch):
    def post(url, json, timeout):
        return httpx.Response(402, json={"reason": "insufficient_funds"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(payments.httpx, "post", post)
    with make_client(tmp_path) as client:
        assert client.post("/orders", json={"customer_id": 1, "amount_cents": 500}).status_code == 402
