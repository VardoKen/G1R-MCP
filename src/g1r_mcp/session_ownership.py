"""Владение управлением общей игрой привязано к MCP-сессии, а не к PID чата."""
from __future__ import annotations
from datetime import datetime, timezone
import threading
import uuid
from typing import Any

from .development import DevelopmentError


class SessionOwnership:
    def __init__(self):
        self.lock = threading.RLock()
        self._owner: Any = None
        self._record: dict | None = None

    def status(self) -> dict:
        with self.lock:
            return {"claimed": self._record is not None,
                    "claim": dict(self._record) if self._record is not None else None,
                    "scope": "development_start_stop_restart_load",
                    "disconnect_releases_claim": False,
                    "automatic_game_stop": False}

    def acquire(self, owner: Any, reason: str, *, process: dict | None = None,
                package: str | None = None) -> dict:
        with self.lock:
            if not isinstance(reason, str) or not 1 <= len(reason) <= 256 or any(ord(c) < 32 for c in reason):
                raise DevelopmentError("invalid_session_reason", "Укажите причину управления общей игрой (1–256 символов)")
            if self._owner is not None and self._owner != owner:
                raise DevelopmentError("game_session_owned", "Общей игрой управляет другая MCP-сессия; сначала завершите её работу")
            if self._record is None:
                self._owner = owner
                self._record = {"claim_id": str(uuid.uuid4()), "reason": reason,
                                "claimed_at": datetime.now(timezone.utc).isoformat(),
                                "process": process, "relative_package": package, "state": "claimed"}
            elif process is not None:
                known = self._record["process"]
                if known is not None and known != process:
                    raise DevelopmentError("session_process_mismatch", "Заявленная игровая сессия относится к другому процессу")
                self._record["process"] = process
            return self.status()

    def require(self, owner: Any, process: dict | None = None) -> None:
        with self.lock:
            if self._owner != owner or self._record is None:
                raise DevelopmentError("game_session_owned" if self._record else "game_session_unclaimed",
                                       "Нужно владение общей игровой сессией; используйте g1r_session_acquire")
            known = self._record["process"]
            if process is not None and known is not None and process != known:
                raise DevelopmentError("session_process_mismatch", "PID, время создания или путь не совпадают с владельцем")

    def bind(self, owner: Any, process: dict, state: str) -> None:
        with self.lock:
            self.require(owner, process)
            self._record.update(process=dict(process), state=state)

    def launched(self, owner: Any, package: str) -> None:
        with self.lock:
            self.require(owner)
            self._record.update(process=None, relative_package=package, state="launch_requested")

    def release(self, owner: Any) -> dict:
        with self.lock:
            self.require(owner)
            previous = self.status()["claim"]
            self._owner = None
            self._record = None
            return {"released": True, "previous_claim": previous, "game_stopped": False}

    def release_abandoned(self, expected_claim_id: str, *, game_running: bool) -> dict:
        with self.lock:
            if game_running:
                raise DevelopmentError("game_running", "Чужая заявка снимается только после подтверждённого завершения игры")
            if self._record is None or self._record["claim_id"] != expected_claim_id:
                raise DevelopmentError("session_claim_changed", "Заявка изменилась после чтения состояния")
            previous = self.status()["claim"]
            self._owner = None
            self._record = None
            return {"released": True, "previous_claim": previous, "game_stopped": False,
                    "recovery": "process_snapshot_empty"}
