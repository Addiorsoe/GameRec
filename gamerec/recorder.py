"""Base class for all stream recorders."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .session import Session

log = logging.getLogger("gamerec")


class Recorder:
    kind = "base"

    def __init__(self) -> None:
        self.session: Session | None = None
        self.status = "idle"  # idle | running | ok | error | unavailable
        self.error: str | None = None
        self.count = 0
        self.files: list[str] = []
        self.extra: dict = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle -------------------------------------------------------
    def start(self, session: Session) -> None:
        self.session = session
        self._stop.clear()
        try:
            self.setup()
        except Exception as e:  # noqa: BLE001 - one stream failing must not stop others
            self._fail(e, unavailable=isinstance(e, (ImportError, OSError)))
            return
        self.status = "running"
        self._thread = threading.Thread(target=self._run_safe, name=f"rec-{self.kind}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        try:
            self.teardown()
        except Exception as e:  # noqa: BLE001
            self._fail(e)
        if self.status == "running":
            self.status = "ok"

    def _run_safe(self) -> None:
        try:
            self.run()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _fail(self, e: BaseException, unavailable: bool = False) -> None:
        self.status = "unavailable" if unavailable else "error"
        self.error = f"{type(e).__name__}: {e}"
        log.exception("%s recorder failed", self.kind)

    # --- helpers ---------------------------------------------------------
    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def path(self, rel: str) -> Path:
        assert self.session is not None
        p = self.session.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel not in self.files:
            self.files.append(rel)
        return p

    def now_ns(self) -> int:
        assert self.session is not None
        return self.session.clock.now_ns()

    # --- to override -----------------------------------------------------
    def setup(self) -> None:
        """Open devices/files. Raise ImportError/OSError if unavailable."""

    def run(self) -> None:
        """Worker loop; return when self.stopping is True."""

    def teardown(self) -> None:
        """Close files."""

    def describe(self) -> dict:
        d = {"kind": self.kind, "status": self.status, "files": self.files, "count": self.count}
        if self.error:
            d["error"] = self.error
        d.update(self.extra)
        return d
