import logging
import sys
from datetime import datetime, timezone

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


class UtcFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(UtcFormatter(LOG_FORMAT))
    root = logging.getLogger("app")
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False
