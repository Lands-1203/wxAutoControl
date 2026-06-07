from __future__ import annotations

from .types import WindowDiagnostic


class _NullWriter:
    def write(self, _: str) -> int:
        return 0

    def flush(self) -> None:
        return None


def _find_descendant_by_class(ctrl, class_name: str, max_depth: int):
    try:
        if getattr(ctrl, "ClassName", "") == class_name:
            return ctrl
    except Exception:
        return None
    if max_depth <= 0:
        return None
    try:
        children = ctrl.GetChildren()
    except Exception:
        return None
    for child in children:
        found = _find_descendant_by_class(child, class_name, max_depth - 1)
        if found is not None:
            return found
    return None


def _find_main_window(uia, nickname: str | None = None):
    root = uia.GetRootControl()
    for cls_name in ("mmui::MainWindow", "WeChatMainWndForPC"):
        try:
            win = root.WindowControl(ClassName=cls_name)
            if win.Exists(maxSearchSeconds=1):
                if nickname is not None and nickname not in getattr(win, "Name", ""):
                    continue
                return win, win
        except Exception:
            continue

    top_candidates = []
    try:
        for name in ("微信", "WeChat"):
            win = root.WindowControl(Name=name)
            if win.Exists(maxSearchSeconds=1):
                top_candidates.append(win)
        win = root.WindowControl(SubName="微信")
        if win.Exists(maxSearchSeconds=2):
            top_candidates.append(win)
    except Exception:
        pass

    seen = set()
    for top_win in top_candidates:
        handle = getattr(top_win, "NativeWindowHandle", 0)
        if handle in seen:
            continue
        seen.add(handle)
        if nickname is not None and nickname not in getattr(top_win, "Name", ""):
            continue
        content = _find_descendant_by_class(top_win, "mmui::MainWindow", max_depth=8)
        if content is not None:
            return top_win, content
        legacy = _find_descendant_by_class(top_win, "WeChatMainWndForPC", max_depth=8)
        if legacy is not None:
            return top_win, legacy
        return top_win, top_win
    return None, None


def detect_weixin_window(nickname: str | None = None) -> WindowDiagnostic:
    from uiautomation import GetRootControl

    class _UiaFacade:
        @staticmethod
        def GetRootControl():
            return GetRootControl()

    top_win, content = _find_main_window(_UiaFacade, nickname=nickname)
    if content is None:
        return WindowDiagnostic(
            client_shape="unknown",
            top_window=None,
            content_window=None,
            reason="未发现微信候选窗口",
        )

    content_cls = getattr(content, "ClassName", "")
    if content_cls == "mmui::MainWindow":
        return WindowDiagnostic("mmui", top_win, content, "")

    webview = _find_descendant_by_class(top_win or content, "Chrome_WidgetWin_0", max_depth=6)
    if webview is not None:
        return WindowDiagnostic(
            "qt-webview-host",
            top_win,
            content,
            "检测到 Qt 外壳窗口下挂 Chromium/WebView 容器，当前使用视觉自动化链路。",
        )

    return WindowDiagnostic(
        "qt-shell-only",
        top_win,
        content,
        "仅检测到 Qt 外壳窗口，未发现 mmui::MainWindow 内容根",
    )
