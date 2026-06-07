from __future__ import annotations

from .client import WeixinController
from .common.types import ControlResponse


def send_message(
    who: str,
    msg: str,
    *,
    exact: bool = False,
    clear: bool = True,
    nickname: str | None = None,
) -> ControlResponse:
    controller = WeixinController(nickname=nickname)
    return controller.send_msg(
        msg=msg,
        who=who,
        exact=exact,
        clear=clear,
    )
