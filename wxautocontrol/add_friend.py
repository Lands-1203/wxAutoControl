from __future__ import annotations

from .client import WeixinController
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
) -> ControlResponse:
    controller = WeixinController(nickname=nickname)
    return controller.add_new_friend(
        phone=phone,
        verify_msg=verify_msg,
        remark=remark,
        tags=tags,
        permission=permission,
        entry_mode=entry_mode,
    )
