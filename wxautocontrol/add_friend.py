from __future__ import annotations

from .client import WeixinController
from .common.log import log
from .common.types import ControlResponse


def add_friend(
    phone: str,
    *,
    verify_msg: str = "",
    remark: str = "",
    tags: list[str] | None = None,
    permission: str = "聊天、朋友圈、微信运动等",
    nickname: str | None = None,
    entry_mode: str = "global_search",
    not_submit: bool = False,
) -> ControlResponse:
    try:
        controller = WeixinController(nickname=nickname)
        return controller.add_new_friend(
            phone=phone,
            verify_msg=verify_msg,
            remark=remark,
            tags=tags,
            permission=permission,
            entry_mode=entry_mode,
            not_submit=not_submit,
        )
    except KeyboardInterrupt:
        log.warning("add_friend interrupted by user")
        return ControlResponse.failure(
            "用户中断执行",
            data={
                "phone": phone,
                "status": "interrupted",
                "entry_mode": entry_mode,
                "not_submit": not_submit,
            },
            code="ADD_FRIEND_INTERRUPTED",
        )
    except Exception as exc:
        log.error(f"add_friend unexpected error: {exc}", exc_info=True)
        return ControlResponse.failure(
            f"执行异常: {type(exc).__name__}",
            data={
                "phone": phone,
                "status": "exception",
                "entry_mode": entry_mode,
                "not_submit": not_submit,
                "exception_type": type(exc).__name__,
            },
            code="ADD_FRIEND_EXCEPTION",
        )
