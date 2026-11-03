import logging
import time

import httpx

from .config import Settings

log = logging.getLogger("app.payments")


class PaymentError(Exception):
    pass


class PaymentDeclined(PaymentError):
    pass


def charge(settings: Settings, customer_id: int, amount_cents: int) -> str:
    url = f"{settings.payments_url}/charge"
    started = time.monotonic()
    try:
        resp = httpx.post(
            url,
            json={"customer_id": customer_id, "amount_cents": amount_cents},
            timeout=settings.payments_timeout_s,
        )
    except httpx.HTTPError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.error(
            "charge failed customer_id=%s url=%s error=%s duration_ms=%d",
            customer_id,
            url,
            exc.__class__.__name__,
            elapsed_ms,
        )
        raise PaymentError(str(exc)) from exc

    if resp.status_code == 402:
        log.warning("charge declined customer_id=%s reason=%s", customer_id, resp.json().get("reason"))
        raise PaymentDeclined(resp.json().get("reason", "declined"))
    if resp.status_code >= 400:
        log.error("charge failed customer_id=%s url=%s status=%d", customer_id, url, resp.status_code)
        raise PaymentError(f"gateway returned {resp.status_code}")

    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.info("charge ok customer_id=%s duration_ms=%d", customer_id, elapsed_ms)
    return resp.json()["charge_id"]
