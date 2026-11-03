import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    app_version: str
    database_path: str
    payments_url: str
    payments_timeout_s: float
    db_pool_size: int
    db_acquire_timeout_s: float
    max_threads: int


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        app_version=env.get("APP_VERSION", "dev"),
        database_path=env.get("DATABASE_PATH", "orders.db"),
        payments_url=env.get("PAYMENTS_URL", "http://localhost:9000"),
        payments_timeout_s=float(env.get("PAYMENTS_TIMEOUT_S", "3.0")),
        db_pool_size=int(env.get("DB_POOL_SIZE", "10")),
        db_acquire_timeout_s=float(env.get("DB_ACQUIRE_TIMEOUT_S", "2.0")),
        max_threads=int(env.get("MAX_THREADS", "12")),
    )
