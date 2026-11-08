"""Optional shared-session metadata without restricting game control."""
from __future__ import annotations
from datetime import datetime, timezone
import threading
import uuid
from typing import Any
from .development import DevelopmentError


class SessionOwnership:
    def __init__(self):
        self.lock = threading.RLock()
        self._record: dict | None = None

    def status(self) -> dict:
        with self.lock:
            return {"claimed": self._record is not None,
                    "claim": dict(self._record) if self._record is not None else None,
                    "scope": "informational",
                    "control_required": False,
                    "ownership_enforced": False,
                    "disconnect_releases_claim": False,
                    "automatic_game_stop": False}

    def acquire(self, owner: Any, reason: str, *, process: dict | None = None,
                package: str | None = None, expected_claim_id: str | None = None) -> dict:
        with self.lock:
            if not isinstance(reason, str):
                raise DevelopmentError("invalid_session_reason", "The session description must be a string")
            if self._record is None:
                self._record = {"claim_id": str(uuid.uuid4()), "reason": reason,
                                "claimed_at": datetime.now(timezone.utc).isoformat(),
                                "process": dict(process) if process is not None else None,
                                "relative_package": package, "state": "claimed"}
            elif process is not None:
                self._record["process"] = dict(process)
            result = self.status()
            if expected_claim_id is not None:
                result["resumed"] = True
            return result

    def require(self, owner: Any, process: dict | None = None) -> None:
        """Compatibility entry point; session metadata does not authorize control."""

    def bind(self, owner: Any, process: dict, state: str) -> None:
        with self.lock:
            if self._record is None:
                self.acquire(owner, "Native bridge attachment", process=process)
            self._record.update(process=dict(process), state=state)

    def launched(self, owner: Any, package: str) -> None:
        with self.lock:
            if self._record is None:
                self.acquire(owner, "Development launch", package=package)
            self._record.update(process=None, relative_package=package, state="launch_requested")

    def release(self, owner: Any) -> dict:
        with self.lock:
            previous = self.status()["claim"]
            self._record = None
            return {"released": previous is not None, "previous_claim": previous, "game_stopped": False}

    def release_abandoned(self, expected_claim_id: str, *, game_running: bool) -> dict:
        """Clear optional metadata without requiring an absent game process."""
        with self.lock:
            return {**self.release(None), "recovery": "informational_claim_release"}
