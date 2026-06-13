from __future__ import annotations

from .client import WeixinController
from .common.log import log
from .common.types import ControlResponse


def send_message(
    who: str,
    msg: str,
    *,
    exact: bool = False,
    clear: bool = True,
    nickname: str | None = None,
) -> ControlResponse:
    try:
        controller = WeixinController(nickname=nickname)
        return controller.send_msg(
            msg=msg,
            who=who,
            exact=exact,
            clear=clear,
        )
    except KeyboardInterrupt:
        log.warning("send_message interrupted by user")
        return ControlResponse.failure(
            "用户中断执行",
            data={
                "who": who,
                "status": "interrupted",
            },
            code="SEND_MESSAGE_INTERRUPTED",
        )
    except Exception as exc:
        log.error(f"send_message unexpected error: {exc}", exc_info=True)
        return ControlResponse.failure(
            f"执行异常: {type(exc).__name__}",
            data={
                "who": who,
                "status": "exception",
                "exception_type": type(exc).__name__,
            },
            code="SEND_MESSAGE_EXCEPTION",
        )


def send_message_continue(
    msg: str,
    *,
    who: str | None = None,
    clear: bool = True,
    nickname: str | None = None,
) -> ControlResponse:
    try:
        controller = WeixinController(nickname=nickname)
        return controller.send_msg_continue(
            msg=msg,
            who=who,
            clear=clear,
        )
    except KeyboardInterrupt:
        log.warning("send_message_continue interrupted by user")
        return ControlResponse.failure(
            "用户中断执行",
            data={
                "who": who,
                "status": "interrupted",
            },
            code="SEND_MESSAGE_INTERRUPTED",
        )
    except Exception as exc:
        log.error(f"send_message_continue unexpected error: {exc}", exc_info=True)
        return ControlResponse.failure(
            f"执行异常: {type(exc).__name__}",
            data={
                "who": who,
                "status": "exception",
                "exception_type": type(exc).__name__,
            },
            code="SEND_MESSAGE_EXCEPTION",
        )
