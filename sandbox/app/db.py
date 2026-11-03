import logging
import queue
import sqlite3

log = logging.getLogger("app.db")


class PoolExhausted(Exception):
    pass


class ConnectionPool:
    """Tiny fixed-size SQLite connection pool."""

    def __init__(self, path: str, size: int, acquire_timeout_s: float) -> None:
        self.size = size
        self.acquire_timeout_s = acquire_timeout_s
        self._idle: queue.Queue[sqlite3.Connection] = queue.Queue(maxsize=size)
        for _ in range(size):
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._idle.put(conn)

    @property
    def in_use(self) -> int:
        return self.size - self._idle.qsize()

    def acquire(self) -> sqlite3.Connection:
        try:
            return self._idle.get(timeout=self.acquire_timeout_s)
        except queue.Empty:
            msg = (
                f"PoolExhausted: no free connection after {self.acquire_timeout_s:.1f}s "
                f"(size={self.size}, in_use={self.in_use})"
            )
            log.error(msg)
            raise PoolExhausted(msg) from None

    def release(self, conn: sqlite3.Connection) -> None:
        self._idle.put(conn)

    def close(self) -> None:
        while not self._idle.empty():
            self._idle.get_nowait().close()
