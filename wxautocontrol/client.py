from __future__ import annotations

import time
from typing import Optional

from .common.log import log
from .common.types import ControlConfig, ControlResponse
from . import state as _state_module
from .common.lock import uilock
from .common.window import detect_weixin_window
from .visual import (
    VisualAddFriendWindow,
    VisualFriendRequestWindow,
    VisualWeixinWindow,
)


class WeixinController:
    """独立于旧 mmui 框架的 Qt 客户端能力封装。

    当前只承载两类能力：
    - 添加好友
    - 发送消息

    不依赖 SessionBox / ChatBox / WeChatMainWnd 的 mmui 业务树。
    """

    def __init__(self, nickname: str | None = None):
        self.nickname = nickname
        self.diag = detect_weixin_window(nickname=nickname)

    @property
    def hwnd(self) -> int:
        return getattr(self.diag.top_window, "NativeWindowHandle", 0) or 0

    def is_available(self) -> bool:
        return bool(self.hwnd)

    def create_visual_window(self) -> VisualWeixinWindow | None:
        if not self.is_available():
            return None
        return VisualWeixinWindow(self.hwnd)

    @staticmethod
    def _add_friend_error_code(status: str) -> str:
        if status == "already_friend":
            return "ADD_FRIEND_ALREADY_ADDED"
        return "ADD_FRIEND_FAILED"

    @uilock
    def send_msg(
        self,
        msg: str,
        who: str,
        *,
        clear: bool = True,
        exact: bool = False,
        at=None,
    ) -> ControlResponse:
        if not isinstance(msg, str) or not msg:
            return ControlResponse.failure(
                "msg 不能为空",
                data={"status": "invalid"},
                code="SEND_MESSAGE_FAILED",
            )
        if not who:
            return ControlResponse.failure(
                "who 不能为空",
                data={"status": "invalid"},
                code="SEND_MESSAGE_FAILED",
            )
        if at:
            return ControlResponse.failure(
                "Qt 客户端独立发送链路暂不支持 at",
                data={"status": "ui_error", "who": who},
                code="SEND_MESSAGE_FAILED",
            )
        visual = self.create_visual_window()
        if visual is None:
            return ControlResponse.failure(
                "无法定位微信主窗口",
                data={"status": "ui_error", "who": who},
                code="SEND_MESSAGE_FAILED",
            )
        log.debug(
            "发送消息开始 "
            f"目标={who!r} 精确匹配={exact} 发送后清空={clear}"
        )
        result = visual.send_message_to_session(
            who=who,
            msg=msg,
            exact=exact,
            clear=clear,
        )
        log.debug(f"发送消息结果 result={result}")
        if result == "sent":
            with _state_module._message_state_lock:
                _state_module._last_message_target = who
            return ControlResponse.success(
                "发送成功",
                data={"status": "sent", "who": who},
                code="SEND_MESSAGE_SUCCESS",
            )
        if result == "session_not_found":
            return ControlResponse.failure(
                f"未找到聊天窗口：{who}",
                data={"status": "not_found", "who": who},
                code="SEND_MESSAGE_FAILED",
            )
        return ControlResponse.failure(
            f"发送失败：{result}",
            data={"status": "ui_error", "who": who},
            code="SEND_MESSAGE_FAILED",
        )

    @uilock
    def send_msg_continue(
        self,
        msg: str,
        who: str | None = None,
        *,
        clear: bool = True,
    ) -> ControlResponse:
        if not isinstance(msg, str) or not msg:
            return ControlResponse.failure(
                "msg 不能为空",
                data={"status": "invalid"},
                code="SEND_MESSAGE_FAILED",
            )
        target = who
        if not target:
            with _state_module._message_state_lock:
                target = _state_module._last_message_target or None
        if not target:
            return ControlResponse.failure(
                "没有可继续发送的最近会话",
                data={"status": "invalid"},
                code="SEND_MESSAGE_FAILED",
            )
        visual = self.create_visual_window()
        if visual is None:
            return ControlResponse.failure(
                "无法定位微信主窗口",
                data={"status": "ui_error", "who": target},
                code="SEND_MESSAGE_FAILED",
            )
        log.debug(f"连续发送消息开始 目标={target!r} 发送后清空={clear}")
        result = visual.send_message_to_current_session(
            msg=msg,
            clear=clear,
        )
        log.debug(f"连续发送消息结果 result={result}")
        if result == "sent":
            with _state_module._message_state_lock:
                _state_module._last_message_target = target
            return ControlResponse.success(
                "发送成功",
                data={"status": "sent", "who": target},
                code="SEND_MESSAGE_SUCCESS",
            )
        return ControlResponse.failure(
            f"发送失败：{result}",
            data={"status": "ui_error", "who": target},
            code="SEND_MESSAGE_FAILED",
        )

    @uilock
    def add_new_friend(
        self,
        phone: str,
        *,
        verify_msg: str = "",
        remark: str = "",
        tags: Optional[list[str]] = None,
        permission: str = "聊天、朋友圈、微信运动等",
        entry_mode: str = "global_search",
        not_submit: bool = False,
    ) -> ControlResponse:
        def _add_friend_failure(message: str, status: str, **extra) -> ControlResponse:
            data = {
                "phone": phone,
                "status": status,
            }
            data.update(extra)
            return ControlResponse.failure(
                message,
                data=data,
                code=self._add_friend_error_code(status),
            )

        def _add_friend_success(message: str, status: str = "applied", **extra) -> ControlResponse:
            data = {
                "phone": phone,
                "status": status,
            }
            data.update(extra)
            return ControlResponse.success(
                message,
                data=data,
                code="ADD_FRIEND_SUCCESS",
            )

        if not isinstance(phone, str):
            return _add_friend_failure("phone 不能为空", "invalid")
        phone = phone.strip()
        if not phone:
            return _add_friend_failure("phone 不能为空", "invalid")
        entry_mode = str(entry_mode or "global_search").strip() or "global_search"
        if entry_mode not in {"global_search", "add_menu"}:
            return _add_friend_failure(
                f"不支持的添加好友入口模式: {entry_mode}",
                "invalid",
                entry_mode=entry_mode,
            )

        log.debug(f"添加好友开始 手机号={phone!r} 入口模式={entry_mode!r}")

        with _state_module._add_friend_state_lock:
            interval = ControlConfig.ADD_FRIEND_INTERVAL
            if interval > 0:
                wait = interval - (time.time() - _state_module._last_add_friend_ts)
                if wait > 0:
                    log.debug(f"添加好友命中风控间隔，等待 {wait:.2f}s")
            else:
                wait = 0

        if wait > 0:
            time.sleep(wait)

        visual = self.create_visual_window()
        if visual is None:
            return _add_friend_failure(
                "无法定位微信主窗口",
                "ui_error",
            )

        if entry_mode == "add_menu":
            log.debug("开始从主窗口加号菜单打开添加朋友窗口")
            opened = visual.open_add_friend_from_menu(phone, retries=3)
            log.debug(f"加号菜单打开添加朋友窗口结果={opened}")
        else:
            log.debug("开始从全局搜索打开添加朋友窗口")
            opened = visual.open_add_friend_from_global_search(phone, retries=3)
            log.debug(f"全局搜索打开添加朋友窗口结果={opened}")
        if not opened:
            return _add_friend_failure(
                "无法打开添加朋友窗口",
                "ui_error",
                entry_mode=entry_mode,
            )

        add_wnd = VisualAddFriendWindow.find(timeout=ControlConfig.ADD_FRIEND_TIMEOUT)
        log.debug(f"是否找到添加朋友窗口={add_wnd is not None}")
        if add_wnd is None:
            return _add_friend_failure(
                "无法打开添加朋友窗口",
                "ui_error",
                entry_mode=entry_mode,
            )

        search_status = add_wnd.detect_result_state()
        log.debug(f"添加朋友资料页状态={search_status}")
        if search_status == "not_found":
            return _add_friend_failure(
                "未找到目标用户",
                "not_found",
                entry_mode=entry_mode,
            )
        if search_status == "unknown":
            return _add_friend_failure(
                "无法确认目标用户资料页状态",
                "ui_error",
                entry_mode=entry_mode,
            )
        if search_status == "already_friend":
            return _add_friend_failure(
                "已是好友",
                "already_friend",
                entry_mode=entry_mode,
            )

        request_form_settle = min(1.2, max(0.8, ControlConfig.ADD_FRIEND_TIMEOUT))
        request_wnd = add_wnd.open_request_form_with_retry(
            retries=3,
            settle=request_form_settle,
            retry_interval=0.5,
        )
        log.debug(f"是否找到申请添加朋友弹窗={request_wnd is not None}")
        if request_wnd is None:
            fallback_state = add_wnd.detect_result_state()
            log.debug(
                "点击添加到通讯录后未找到申请弹窗，"
                f"回退检测资料页状态={fallback_state}"
            )
            if fallback_state == "pending_verification":
                with _state_module._add_friend_state_lock:
                    _state_module._last_add_friend_ts = time.time()
                return _add_friend_success(
                    "申请已发送",
                    entry_mode=entry_mode,
                )
            if fallback_state == "already_friend":
                return _add_friend_failure(
                    "已是好友",
                    "already_friend",
                    entry_mode=entry_mode,
                )
            if fallback_state == "not_found":
                return _add_friend_failure(
                    "未找到目标用户",
                    "not_found",
                    entry_mode=entry_mode,
                )
            return _add_friend_failure(
                "UI 控件定位失败: 添加到通讯录按钮",
                "ui_error",
                entry_mode=entry_mode,
            )

        tags_value: str | list[str] = ""
        if isinstance(tags, list):
            tags_value = [str(item).strip() for item in tags if str(item).strip()]
        elif tags:
            tags_value = str(tags).strip()

        submitted = request_wnd.apply_form(
            verify_msg=verify_msg,
            remark=remark,
            tags=tags_value,
            permission=permission,
            settle=0.8,
        ) if not not_submit else request_wnd.fill_form(
            verify_msg=verify_msg,
            remark=remark,
            tags=tags_value,
            permission=permission,
        )
        log.debug(f"提交申请表单结果={submitted} not_submit={not_submit}")
        if not submitted:
            return _add_friend_failure(
                "UI 控件定位失败: 确定按钮" if not not_submit else "UI 控件定位失败: 申请表单填写",
                "ui_error",
                entry_mode=entry_mode,
            )

        if not_submit:
            return ControlResponse.success(
                "已停在发送申请前",
                data={
                    "phone": phone,
                    "status": "ready_to_submit",
                    "entry_mode": entry_mode,
                    "not_submit": True,
                },
                code="ADD_FRIEND_NOT_SUBMITTED",
            )

        post_state = add_wnd.infer_post_submit_success(
            submit_ok=submitted,
            timeout=max(2.5, ControlConfig.ADD_FRIEND_TIMEOUT),
        )
        log.debug(f"提交后的页面状态={post_state}")
        if post_state == "applied":
            with _state_module._add_friend_state_lock:
                _state_module._last_add_friend_ts = time.time()
            return _add_friend_success(
                "申请已发送",
                entry_mode=entry_mode,
            )
        if post_state == "rate_limited":
            with _state_module._add_friend_state_lock:
                _state_module._last_add_friend_ts = time.time()
            return _add_friend_failure(
                "操作过于频繁，请稍后再试",
                "rate_limited",
                entry_mode=entry_mode,
            )
        if post_state == "permission_required":
            return _add_friend_failure(
                "对方联系人较多，需调整朋友权限后重试",
                "permission_required",
                entry_mode=entry_mode,
            )
        if post_state == "request_form":
            return _add_friend_failure(
                "申请添加朋友弹窗仍未完成",
                "ui_error",
                entry_mode=entry_mode,
            )
        if post_state == "send_form":
            return _add_friend_failure(
                "提交后仍停留在申请表单",
                "ui_error",
                entry_mode=entry_mode,
            )
        return _add_friend_failure(
            f"提交后状态未确认: {post_state}",
            "ui_error",
            entry_mode=entry_mode,
        )
