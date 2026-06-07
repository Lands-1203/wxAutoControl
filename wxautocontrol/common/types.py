from __future__ import annotations

from dataclasses import dataclass
import threading


@dataclass
class WindowDiagnostic:
    client_shape: str
    top_window: object | None
    content_window: object | None
    reason: str = ""


class ControlConfig:
    ENABLE_FILE_LOGGER: bool = True
    ADD_FRIEND_TIMEOUT: float = 5.0
    ADD_FRIEND_INTERVAL: float = 30.0
    FIXED_MAIN_WINDOW_WIDTH: int = 1000
    FIXED_MAIN_WINDOW_HEIGHT: int = 700


_last_add_friend_ts: float = 0.0
_add_friend_state_lock: threading.Lock = threading.Lock()


class ControlResponse(dict):
    def __init__(self, status: str, message: str, data: dict | None = None):
        super().__init__(status=status, message=message, data=data)

    @property
    def is_success(self) -> bool:
        return self["status"] == "成功"

    @classmethod
    def success(cls, message: str | None = None, data: dict | None = None):
        return cls("成功", message or "", data)

    @classmethod
    def failure(cls, message: str, data: dict | None = None):
        return cls("失败", message, data)
