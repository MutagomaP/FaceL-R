# src/session_log.py
"""CSV session log for assessment evidence (speaker, confidence, motor commands)."""
from __future__ import annotations

import csv
import time
from pathlib import Path

# Internal MQTT/ESP statuses -> BENAX spec terminology (for logs & payload field)
SPEC_STATUS_MAP = {
    "MOVE_LEFT": "MOVED_LEFT",
    "MOVE_RIGHT": "MOVED_RIGHT",
    "CENTERED": "CENTERED",
    "NO_FACE": "OUT_OF_FRAME",
}


def to_spec_status(motor_status: str) -> str:
    return SPEC_STATUS_MAP.get(motor_status, motor_status)


class SessionLogger:
    def __init__(self, speaker_id: str, log_dir: Path | None = None):
        log_dir = log_dir or Path("data/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c for c in speaker_id if c.isalnum() or c in ("_", "-"))
        self.path = log_dir / f"session_{safe}_{ts}.csv"
        self._file = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(
            [
                "timestamp",
                "speaker_id",
                "confidence",
                "motor_status",
                "spec_status",
                "locked",
            ]
        )
        self._file.flush()
        print(f"[SessionLog] Writing to {self.path}")

    def write(
        self,
        speaker_id: str,
        confidence: float,
        motor_status: str,
        locked: bool,
    ) -> None:
        self._writer.writerow(
            [
                time.strftime("%Y-%m-%d %H:%M:%S"),
                speaker_id,
                f"{confidence:.4f}",
                motor_status,
                to_spec_status(motor_status),
                int(locked),
            ]
        )
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def resolve_speaker_name(requested: str, db_names: list[str]) -> str:
    """Case-insensitive match against enrolled names."""
    if requested in db_names:
        return requested
    req = requested.lower()
    for name in db_names:
        if name.lower() == req:
            return name
    return requested
