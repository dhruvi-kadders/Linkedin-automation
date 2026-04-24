from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .db import logs


class WorkerLogger:
    def __init__(
        self,
        account_id: str,
        emit: Callable[[str, dict[str, Any]], None],
        label: str = "",
    ) -> None:
        self.account_id = account_id
        self.label = label
        self._emit = emit

    def _format(self, level: str, message: str, meta: dict[str, Any] | None) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        prefix = f"[{self.label}] " if self.label else ""
        print(f"{ts} {level.upper()} {prefix}{message}", meta or "")

    def _persist(self, level: str, message: str, meta: dict[str, Any] | None) -> None:
        if level == "debug":
            return
        try:
            logs.insert(self.account_id, level, message, meta)
        except Exception:
            pass

    def _write(self, level: str, message: str, meta: dict[str, Any] | None = None) -> None:
        self._format(level, message, meta)
        self._persist(level, message, meta)
        self._emit("log", {"level": level, "message": message, "meta": meta})

    def info(self, message: str, meta: dict[str, Any] | None = None) -> None:
        self._write("info", message, meta)

    def warn(self, message: str, meta: dict[str, Any] | None = None) -> None:
        self._write("warn", message, meta)

    def error(self, message: str, meta: dict[str, Any] | None = None) -> None:
        self._write("error", message, meta)

    def debug(self, message: str, meta: dict[str, Any] | None = None) -> None:
        self._write("debug", message, meta)

