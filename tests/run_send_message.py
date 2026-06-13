from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wxautocontrol.common.log import log
from wxautocontrol.send_message import send_message


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行独立版发送消息脚本")
    parser.add_argument("--who", required=True)
    parser.add_argument("--msg", required=True)
    parser.add_argument("--exact", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        if args.debug:
            log.set_debug(True)
        print("[独立发送消息脚本] 运行参数")
        print(json.dumps({
            "who": args.who,
            "msg": args.msg,
            "exact": args.exact,
        }, ensure_ascii=False, indent=2))
        resp = send_message(
            who=args.who,
            msg=args.msg,
            exact=args.exact,
        )
        print("[独立发送消息脚本] 返回结果")
        print(json.dumps(dict(resp), ensure_ascii=False, indent=2))
        return 0 if resp.is_success else 2
    except KeyboardInterrupt:
        print("[独立发送消息脚本] 返回结果")
        print(json.dumps({
            "status": "error",
            "message": "用户中断执行",
            "code": "SEND_MESSAGE_INTERRUPTED",
            "data": {
                "status": "interrupted",
            },
        }, ensure_ascii=False, indent=2))
        return 130
    except Exception as exc:
        log.error(f"run_send_message unexpected error: {exc}", exc_info=True)
        print("[独立发送消息脚本] 返回结果")
        print(json.dumps({
            "status": "error",
            "message": f"执行异常: {type(exc).__name__}",
            "code": "SEND_MESSAGE_EXCEPTION",
            "data": {
                "status": "exception",
                "exception_type": type(exc).__name__,
            },
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
