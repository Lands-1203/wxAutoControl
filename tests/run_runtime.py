from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wxautocontrol.client import WeixinController
from wxautocontrol.common.log import log
from wxautocontrol.common.types import ControlResponse


class RuntimeConsole:
    def __init__(self, nickname: str | None = None, debug: bool = False):
        self.nickname = nickname
        self.debug = debug
        self.controller: WeixinController | None = None

    def ensure_controller(self, refresh: bool = False) -> WeixinController:
        if refresh or self.controller is None:
            self.controller = WeixinController(nickname=self.nickname)
        return self.controller

    def warmup(self) -> ControlResponse:
        controller = self.ensure_controller(refresh=True)
        if not controller.is_available():
            return ControlResponse.failure(
                "无法定位微信主窗口",
                data={"status": "ui_error"},
                code="RUNTIME_WARMUP_FAILED",
            )
        visual = controller.create_visual_window()
        if visual is None:
            return ControlResponse.failure(
                "无法创建视觉窗口控制器",
                data={"status": "ui_error"},
                code="RUNTIME_WARMUP_FAILED",
            )
        image = visual._search_region_image()
        visual.ocr_texts(image=image)
        return ControlResponse.success(
            "预热完成",
            data={
                "status": "ready",
                "hwnd": controller.hwnd,
                "nickname": self.nickname,
            },
            code="RUNTIME_READY",
        )

    def refresh(self) -> ControlResponse:
        controller = self.ensure_controller(refresh=True)
        return ControlResponse.success(
            "控制器已刷新",
            data={
                "status": "ready" if controller.is_available() else "ui_error",
                "hwnd": controller.hwnd,
                "nickname": self.nickname,
            },
            code="RUNTIME_REFRESHED",
        )

    def send(self, argv: list[str]) -> ControlResponse:
        parser = argparse.ArgumentParser(prog="send", add_help=False)
        parser.add_argument("--who", required=True)
        parser.add_argument("--msg", required=True)
        parser.add_argument("--exact", action="store_true")
        parser.add_argument("--no-clear", action="store_true")
        args = parser.parse_args(argv)
        controller = self.ensure_controller()
        if not controller.is_available():
            controller = self.ensure_controller(refresh=True)
        return controller.send_msg(
            who=args.who,
            msg=args.msg,
            exact=args.exact,
            clear=not args.no_clear,
        )

    def send_continue(self, argv: list[str]) -> ControlResponse:
        parser = argparse.ArgumentParser(prog="sendc", add_help=False)
        parser.add_argument("--msg", required=True)
        parser.add_argument("--who")
        parser.add_argument("--no-clear", action="store_true")
        args = parser.parse_args(argv)
        controller = self.ensure_controller()
        if not controller.is_available():
            controller = self.ensure_controller(refresh=True)
        return controller.send_msg_continue(
            msg=args.msg,
            who=args.who,
            clear=not args.no_clear,
        )

    def add(self, argv: list[str]) -> ControlResponse:
        parser = argparse.ArgumentParser(prog="add", add_help=False)
        parser.add_argument("--phone", required=True)
        parser.add_argument("--verify-msg", default="")
        parser.add_argument("--remark", default="")
        parser.add_argument("--tag", action="append", default=[])
        parser.add_argument("--permission", default="聊天、朋友圈、微信运动等")
        parser.add_argument("--entry-mode", default="global_search")
        parser.add_argument("--not-submit", action="store_true")
        args = parser.parse_args(argv)
        controller = self.ensure_controller()
        if not controller.is_available():
            controller = self.ensure_controller(refresh=True)
        return controller.add_new_friend(
            phone=args.phone,
            verify_msg=args.verify_msg,
            remark=args.remark,
            tags=args.tag,
            permission=args.permission,
            entry_mode=args.entry_mode,
            not_submit=args.not_submit,
        )

    def run_command(self, line: str) -> ControlResponse:
        parts = shlex.split(line)
        if not parts:
            return ControlResponse.success(
                "",
                data={"status": "noop"},
                code="NOOP",
            )
        cmd, *argv = parts
        if cmd == "warmup":
            return self.warmup()
        if cmd == "refresh":
            return self.refresh()
        if cmd == "send":
            return self.send(argv)
        if cmd in {"sendc", "send-continue"}:
            return self.send_continue(argv)
        if cmd == "add":
            return self.add(argv)
        if cmd in {"exit", "quit"}:
            raise EOFError
        return ControlResponse.failure(
            f"未知命令: {cmd}",
            data={"status": "invalid_command"},
            code="RUNTIME_INVALID_COMMAND",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="常驻运行 wxAutoControl 控制台")
    parser.add_argument("--nickname")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--warmup", action="store_true")
    return parser


def print_response(resp: ControlResponse) -> None:
    print(json.dumps(dict(resp), ensure_ascii=False, indent=2))


def main() -> int:
    args = build_parser().parse_args()
    if args.debug:
        log.set_debug(True)
    console = RuntimeConsole(nickname=args.nickname, debug=args.debug)
    print("[wxAutoControl runtime] ready")
    print("[wxAutoControl runtime] commands: warmup | refresh | send ... | sendc ... | add ... | exit")
    if args.warmup:
        print_response(console.warmup())
    while True:
        try:
            line = input("wx> ").strip()
            if not line:
                continue
            resp = console.run_command(line)
            print_response(resp)
        except EOFError:
            print("[wxAutoControl runtime] bye")
            return 0
        except KeyboardInterrupt:
            print_response(
                ControlResponse.failure(
                    "用户中断执行",
                    data={"status": "interrupted"},
                    code="RUNTIME_INTERRUPTED",
                )
            )
        except SystemExit:
            print_response(
                ControlResponse.failure(
                    "命令参数错误",
                    data={"status": "invalid_args"},
                    code="RUNTIME_INVALID_ARGS",
                )
            )
        except Exception as exc:
            log.error(f"runtime unexpected error: {exc}", exc_info=True)
            print_response(
                ControlResponse.failure(
                    f"执行异常: {type(exc).__name__}",
                    data={
                        "status": "exception",
                        "exception_type": type(exc).__name__,
                    },
                    code="RUNTIME_EXCEPTION",
                )
            )


if __name__ == "__main__":
    raise SystemExit(main())
