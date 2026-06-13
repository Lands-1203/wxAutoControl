from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wxautocontrol.add_friend import add_friend
from wxautocontrol.common.log import log


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行独立版添加好友脚本")
    parser.add_argument("--phone", required=True)
    parser.add_argument("--verify-msg", default="")
    parser.add_argument("--remark", default="")
    parser.add_argument("--permission", default="聊天、朋友圈、微信运动等")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--entry-mode", choices=["global_search", "add_menu"], default="global_search")
    parser.add_argument("--not-submit", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        if args.debug:
            log.set_debug(True)
        print("[独立添加好友脚本] 运行参数")
        print(json.dumps({
            "phone": args.phone,
            "verify_msg": args.verify_msg,
            "remark": args.remark,
            "tags": args.tag,
            "permission": args.permission,
            "entry_mode": args.entry_mode,
            "not_submit": args.not_submit,
        }, ensure_ascii=False, indent=2))
        resp = add_friend(
            phone=args.phone,
            verify_msg=args.verify_msg,
            remark=args.remark,
            tags=args.tag,
            permission=args.permission,
            entry_mode=args.entry_mode,
            not_submit=args.not_submit,
        )
        print("[独立添加好友脚本] 返回结果")
        print(json.dumps(dict(resp), ensure_ascii=False, indent=2))
        return 0 if resp.is_success else 2
    except KeyboardInterrupt:
        print("[独立添加好友脚本] 返回结果")
        print(json.dumps({
            "status": "error",
            "message": "用户中断执行",
            "code": "ADD_FRIEND_INTERRUPTED",
            "data": {
                "status": "interrupted",
            },
        }, ensure_ascii=False, indent=2))
        return 130
    except Exception as exc:
        log.error(f"run_add_friend unexpected error: {exc}", exc_info=True)
        print("[独立添加好友脚本] 返回结果")
        print(json.dumps({
            "status": "error",
            "message": f"执行异常: {type(exc).__name__}",
            "code": "ADD_FRIEND_EXCEPTION",
            "data": {
                "status": "exception",
                "exception_type": type(exc).__name__,
            },
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
