"""Visual automation helpers for Weixin clients without UIA business trees.

This module is intentionally small. It provides the minimum primitives needed
to drive the visible Weixin window by screenshot, relative coordinates and
keyboard input when ``mmui::*`` controls are not exposed to Windows UIA.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
import re
from typing import Iterable

import cv2
import numpy as np
import win32api
import win32con
import win32gui
from rapidocr_onnxruntime import RapidOCR

from .exceptions import UiNotFoundError
from .common.log import log
from .common.lock import uilock
from .common.win32 import SetClipboardText, capture


_VISUAL_CACHE_PATH = Path(".task/runtime/visual-weixin-cache.json")
_REFERENCE_WINDOW_WIDTH = 376
_REFERENCE_WINDOW_HEIGHT = 668
from .common.types import ControlConfig

_FIXED_MAIN_WINDOW_WIDTH = ControlConfig.FIXED_MAIN_WINDOW_WIDTH
_FIXED_MAIN_WINDOW_HEIGHT = ControlConfig.FIXED_MAIN_WINDOW_HEIGHT


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class OCRTextBox:
    text: str
    score: float
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class VisualWeixinWindow:
    """Operate the visible main Weixin window by geometry.

    Coordinates are relative to the captured window image. This keeps the
    interaction stable across screen positions as long as the visible layout
    remains the same.
    """

    _shared_ocr: RapidOCR | None = None

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._logged_first_screenshot = False
        self._logged_first_ocr_call = False

    @classmethod
    def _load_visual_cache(cls) -> dict:
        try:
            if _VISUAL_CACHE_PATH.exists():
                return json.loads(_VISUAL_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug(f"VisualWeixinWindow._load_visual_cache failed: {exc}")
        return {}

    @classmethod
    def _save_visual_cache(cls, data: dict) -> None:
        try:
            _VISUAL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _VISUAL_CACHE_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.debug(f"VisualWeixinWindow._save_visual_cache failed: {exc}")

    def _cache_scope_key(self) -> str:
        rect = self.rect
        return f"{self.__class__.__name__}:{rect.width}x{rect.height}"

    def _cache_shared_scope_key(self) -> str:
        return self.__class__.__name__

    def _point_to_ratio(self, point: tuple[int, int]) -> tuple[float, float]:
        rect = self.rect
        width = max(rect.width, 1)
        height = max(rect.height, 1)
        return (
            max(0.0, min(1.0, point[0] / width)),
            max(0.0, min(1.0, point[1] / height)),
        )

    def _ratio_to_point(self, ratio: tuple[float, float]) -> tuple[int, int]:
        rect = self.rect
        rel_x = min(max(int(round(rect.width * ratio[0])), 0), max(rect.width - 1, 0))
        rel_y = min(max(int(round(rect.height * ratio[1])), 0), max(rect.height - 1, 0))
        return rel_x, rel_y

    def _parse_cached_ratio(self, payload) -> tuple[float, float] | None:
        if not isinstance(payload, dict):
            return None
        ratio = payload.get("ratio")
        if (
            isinstance(ratio, list)
            and len(ratio) == 2
            and all(isinstance(v, (int, float)) for v in ratio)
        ):
            return float(ratio[0]), float(ratio[1])
        return None

    @staticmethod
    def _point_in_bounds(
        point: tuple[int, int],
        bounds: tuple[int, int, int, int],
        *,
        pad_x: int = 0,
        pad_y: int = 0,
    ) -> bool:
        left, top, right, bottom = bounds
        return (
            left - pad_x <= point[0] <= right + pad_x
            and top - pad_y <= point[1] <= bottom + pad_y
        )

    def _scaled_x(self, value: int, minimum: int = 1) -> int:
        rect = self.rect
        scaled = int(round(value * rect.width / _REFERENCE_WINDOW_WIDTH))
        return max(minimum, scaled)

    def _scaled_y(self, value: int, minimum: int = 1) -> int:
        rect = self.rect
        scaled = int(round(value * rect.height / _REFERENCE_WINDOW_HEIGHT))
        return max(minimum, scaled)

    def _log_point_source(
        self,
        step: str,
        source: str,
        point: tuple[int, int] | None = None,
        extra: str = "",
    ) -> None:
        payload = f"point={point}" if point is not None else "point=None"
        suffix = f" {extra}" if extra else ""
        log.debug(
            f"[locator] step={step} source={source} scope={self._cache_scope_key()} {payload}{suffix}"
        )

    def _get_cached_point(self, step: str) -> tuple[int, int] | None:
        data = self._load_visual_cache()
        scope_key = self._cache_scope_key()
        scope = data.get(scope_key, {})
        point = scope.get(step)
        if (
            isinstance(point, list)
            and len(point) == 2
            and all(isinstance(v, int) for v in point)
        ):
            resolved = (int(point[0]), int(point[1]))
            self._log_point_source(step, "cache:absolute", resolved)
            return resolved
        ratio = self._parse_cached_ratio(point)
        if ratio is not None:
            resolved = self._ratio_to_point(ratio)
            self._log_point_source(step, "cache:ratio", resolved)
            return resolved
        shared_scope_key = self._cache_shared_scope_key()
        shared_scope = data.get(shared_scope_key, {})
        shared_ratio = self._parse_cached_ratio(shared_scope.get(step))
        if shared_ratio is not None:
            resolved = self._ratio_to_point(shared_ratio)
            self._log_point_source(step, "cache:ratio-shared", resolved)
            return resolved
        log.debug(f"[locator] step={step} source=cache:miss scope={self._cache_scope_key()}")
        return None

    def _get_cached_point_in_bounds(
        self,
        step: str,
        bounds: tuple[int, int, int, int],
        *,
        pad_x: int = 0,
        pad_y: int = 0,
    ) -> tuple[int, int] | None:
        point = self._get_cached_point(step)
        if point is None:
            return None
        if self._point_in_bounds(point, bounds, pad_x=pad_x, pad_y=pad_y):
            return point
        log.debug(
            f"[locator] step={step} source=cache:out-of-bounds "
            f"scope={self._cache_scope_key()} point={point} bounds={bounds}"
        )
        self._clear_cached_point(step)
        return None

    def _set_cached_point(self, step: str, point: tuple[int, int]) -> None:
        data = self._load_visual_cache()
        scope_key = self._cache_scope_key()
        scope = data.setdefault(scope_key, {})
        scope[step] = [int(point[0]), int(point[1])]
        shared_scope_key = self._cache_shared_scope_key()
        shared_scope = data.setdefault(shared_scope_key, {})
        ratio_x, ratio_y = self._point_to_ratio(point)
        shared_scope[step] = {
            "ratio": [round(ratio_x, 6), round(ratio_y, 6)],
            "basis": [self.rect.width, self.rect.height],
        }
        self._save_visual_cache(data)
        log.debug(
            f"[cache store] scope={scope_key} step={step} point={point} "
            f"ratio={[round(ratio_x, 6), round(ratio_y, 6)]}"
        )

    def _clear_cached_point(self, step: str) -> None:
        data = self._load_visual_cache()
        changed = False
        for scope_key in (self._cache_scope_key(), self._cache_shared_scope_key()):
            scope = data.get(scope_key, {})
            if step in scope:
                scope.pop(step, None)
                if scope:
                    data[scope_key] = scope
                else:
                    data.pop(scope_key, None)
                changed = True
        if changed:
            self._save_visual_cache(data)
            log.debug(
                f"[cache clear] scope={self._cache_scope_key()}|{self._cache_shared_scope_key()} step={step}"
            )

    @classmethod
    def _visible_windows(
        cls,
        window_class: str | None = None,
        title: str | None = None,
    ) -> list[int]:
        matches: list[int] = []

        def _enum(hwnd, bag):
            if not win32gui.IsWindowVisible(hwnd):
                return
            cls_name = win32gui.GetClassName(hwnd)
            win_title = win32gui.GetWindowText(hwnd)
            if window_class is not None and cls_name != window_class:
                return
            if title is not None and win_title != title:
                return
            bag.append(hwnd)

        win32gui.EnumWindows(_enum, matches)
        return matches

    @classmethod
    def _find_window_by_title(
        cls,
        window_class: str,
        title: str,
        timeout: float,
    ) -> "VisualWeixinWindow | None":
        deadline = time.time() + timeout
        while time.time() < deadline:
            matches = cls._visible_windows(window_class=window_class, title=title)
            if matches:
                return cls(matches[0])
            time.sleep(0.08)
        return None

    @classmethod
    def _find_window_by_ocr(
        cls,
        window_class: str,
        required_texts: list[str],
        timeout: float,
    ) -> "VisualWeixinWindow | None":
        deadline = time.time() + timeout
        normalized_required = [cls._normalize_text(text) for text in required_texts]
        while time.time() < deadline:
            for hwnd in cls._visible_windows(window_class=window_class):
                try:
                    wnd = cls(hwnd)
                    image = wnd.screenshot()
                    texts = [cls._normalize_text(box.text) for box in wnd.ocr_texts(image=image)]
                    merged = " ".join(texts)
                    if all(req in merged for req in normalized_required):
                        return wnd
                except Exception:
                    continue
            time.sleep(0.1)
        return None

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", "", text or "").strip()

    @classmethod
    def _normalize_for_match(cls, text: str) -> str:
        text = cls._normalize_text(text)
        return text.replace("Q", "").replace("?", "").replace("×", "").replace("x", "")

    def _resolve_abs_point(self, rel_x: int, rel_y: int) -> tuple[int, int]:
        try:
            client_left, client_top = win32gui.ClientToScreen(self.hwnd, (0, 0))
            abs_point = (int(client_left + rel_x), int(client_top + rel_y))
            log.debug(
                f"VisualWeixinWindow._resolve_abs_point source=client-origin rel=({rel_x}, {rel_y}) abs={abs_point}"
            )
            return abs_point
        except Exception:
            rect = self.rect
            abs_point = (rect.left + rel_x, rect.top + rel_y)
            log.debug(
                f"VisualWeixinWindow._resolve_abs_point source=window-origin rel=({rel_x}, {rel_y}) abs={abs_point}"
            )
            return abs_point

    def _left_click_abs(self, abs_x: int, abs_y: int) -> None:
        win32api.SetCursorPos((abs_x, abs_y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, abs_x, abs_y, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, abs_x, abs_y, 0, 0)

    def _move_resize_window(self, left: int, top: int, width: int, height: int) -> None:
        win32gui.MoveWindow(self.hwnd, int(left), int(top), int(width), int(height), True)

    @property
    def rect(self) -> WindowRect:
        left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
        return WindowRect(left, top, right, bottom)

    @uilock
    def activate(self, settle: float = 0.35) -> None:
        try:
            placement = win32gui.GetWindowPlacement(self.hwnd)
            show_cmd = placement[1] if isinstance(placement, tuple) and len(placement) > 1 else None
        except Exception:
            show_cmd = None
        if show_cmd == win32con.SW_SHOWMINIMIZED:
            log.debug("VisualWeixinWindow.activate: restoring minimized window")
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            # Some desktop states may reject SetForegroundWindow. The window is
            # still visible; later clicks may activate it.
            log.debug("VisualWeixinWindow.activate: SetForegroundWindow failed")
        time.sleep(settle)

    @uilock
    def normalize_main_window(
        self,
        width: int = _FIXED_MAIN_WINDOW_WIDTH,
        height: int = _FIXED_MAIN_WINDOW_HEIGHT,
        settle: float = 0.18,
    ) -> WindowRect:
        rect = self.rect
        if rect.width == width and rect.height == height:
            log.debug(
                "VisualWeixinWindow.normalize_main_window "
                f"source=already-normalized size={rect.width}x{rect.height}"
            )
            return rect
        log.debug(
            "VisualWeixinWindow.normalize_main_window "
            f"source=resize from={rect.width}x{rect.height} to={width}x{height}"
        )
        self._move_resize_window(rect.left, rect.top, width, height)
        time.sleep(settle)
        new_rect = self.rect
        log.debug(
            "VisualWeixinWindow.normalize_main_window "
            f"source=resize-done size={new_rect.width}x{new_rect.height}"
        )
        return new_rect

    def screenshot(self, save_path: str | Path | None = None):
        started = time.perf_counter()
        rect = self.rect
        img = capture(self.hwnd, (rect.left, rect.top, rect.right, rect.bottom))
        if not self._logged_first_screenshot:
            self._logged_first_screenshot = True
            log.debug(
                "VisualWeixinWindow.screenshot first capture "
                f"hwnd={self.hwnd} size={rect.width}x{rect.height} "
                f"cost={time.perf_counter() - started:.3f}s"
            )
        if save_path:
            path = Path(save_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path)
        return img

    def _get_ocr(self) -> RapidOCR:
        if VisualWeixinWindow._shared_ocr is None:
            started = time.perf_counter()
            log.debug("VisualWeixinWindow._get_ocr init start")
            VisualWeixinWindow._shared_ocr = RapidOCR()
            log.debug(
                f"VisualWeixinWindow._get_ocr init done cost={time.perf_counter() - started:.3f}s"
            )
        return VisualWeixinWindow._shared_ocr

    def ocr_texts(self, image=None) -> list[OCRTextBox]:
        if image is None:
            image = self.screenshot()
        started = time.perf_counter()
        bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        result, _ = self._get_ocr()(bgr)
        if not self._logged_first_ocr_call:
            self._logged_first_ocr_call = True
            log.debug(
                f"VisualWeixinWindow.ocr_texts first OCR call cost={time.perf_counter() - started:.3f}s"
            )
        boxes: list[OCRTextBox] = []
        for item in result or []:
            points, text, score = item
            xs = [int(p[0]) for p in points]
            ys = [int(p[1]) for p in points]
            boxes.append(
                OCRTextBox(
                    text=text,
                    score=float(score),
                    left=min(xs),
                    top=min(ys),
                    right=max(xs),
                    bottom=max(ys),
                )
            )
        return boxes

    @staticmethod
    def _find_text_box_in_boxes(
        target: str,
        boxes: Iterable[OCRTextBox],
        *,
        exact: bool = False,
    ) -> OCRTextBox | None:
        best: OCRTextBox | None = None
        for box in boxes:
            matched = box.text == target if exact else target in box.text
            if not matched:
                continue
            if best is None or box.score > best.score:
                best = box
        return best

    def find_text_box(self, target: str, image=None, exact: bool = False) -> OCRTextBox | None:
        return self._find_text_box_in_boxes(
            target,
            self.ocr_texts(image=image),
            exact=exact,
        )

    def read_search_text(self) -> str:
        boxes = self._search_text_boxes()
        merged = "".join(box.text for box in boxes)
        merged = self._normalize_for_match(merged)
        merged = merged.strip("+＋")
        if merged in {"", "搜索"}:
            log.debug(
                f"VisualWeixinWindow.read_search_text normalized='' raw_boxes={[box.text for box in boxes]!r}"
            )
            return ""
        log.debug(
            f"VisualWeixinWindow.read_search_text normalized={merged!r} raw_boxes={[box.text for box in boxes]!r}"
        )
        return merged

    def _search_region_box(self) -> tuple[int, int, int, int]:
        rect = self.rect
        left = int(rect.width * 0.05)
        top = int(rect.height * 0.008)
        right = int(rect.width * 0.40)
        bottom = int(rect.height * 0.10)
        return left, top, right, bottom

    def _search_region_image(self, image=None):
        if image is None:
            image = self.screenshot()
        return image.crop(self._search_region_box())

    def _crop_box(self, box: tuple[int, int, int, int], image=None):
        if image is None:
            image = self.screenshot()
        return image.crop(box)

    def _search_text_boxes(self, image=None) -> list[OCRTextBox]:
        crop = self._search_region_image(image=image)
        region_left, region_top, region_right, _ = self._search_region_box()
        region_width = max(region_right - region_left, 1)
        text_min_x = int(region_width * 0.10)
        text_max_x = int(region_width * 0.58)
        kept: list[OCRTextBox] = []
        for box in self.ocr_texts(image=crop):
            normalized = self._normalize_text(box.text)
            if not normalized:
                continue
            if box.right <= text_min_x:
                continue
            if box.left >= text_max_x and len(normalized) <= 2:
                continue
            if normalized in {"Q", "?", "X", "x", "×", "tt", "+", "＋"} and len(normalized) <= 2:
                continue
            kept.append(box)
        kept.sort(key=lambda item: (item.left, item.top))
        return kept

    def _is_search_focused(self, image=None) -> bool:
        search_box = self._find_search_input_box(image=image)
        if search_box is None:
            crop = np.array(self._search_region_image(image=image))
        else:
            crop = np.array(self._crop_box(
                (
                    search_box.left,
                    search_box.top,
                    search_box.right,
                    search_box.bottom,
                ),
                image=image,
            ))
        if crop.size == 0:
            return False
        border = np.concatenate(
            [
                crop[:3, :, :].reshape(-1, 3),
                crop[-3:, :, :].reshape(-1, 3),
                crop[:, :3, :].reshape(-1, 3),
                crop[:, -3:, :].reshape(-1, 3),
            ],
            axis=0,
        )
        green_mask = (
            (border[:, 1] > 120)
            & (border[:, 1] - border[:, 0] > 40)
            & (border[:, 1] - border[:, 2] > 20)
        )
        return int(green_mask.sum()) >= max(24, border.shape[0] // 40)

    def _find_search_input_box(self, image=None) -> OCRTextBox | None:
        if image is None:
            image = self.screenshot()
        crop = self._search_region_image(image=image)
        region_left, region_top, region_right, region_bottom = self._search_region_box()
        placeholder = self.find_search_placeholder(image=image)
        if placeholder is None:
            log.debug("未识别到搜索占位词，无法根据 OCR 推导搜索框")
            return None
        left = max(region_left + max(placeholder.left - self._scaled_x(26), 0), region_left + self._scaled_x(4))
        top = max(region_top + max(placeholder.top - self._scaled_y(12), 0), region_top + self._scaled_y(2))
        right_limit = min(region_right - self._scaled_x(28), region_left + crop.size[0] - self._scaled_x(6))
        right = min(
            region_left + placeholder.right + max(self._scaled_x(120), int(placeholder.width * 4.8)),
            right_limit,
        )
        bottom = min(
            region_top + placeholder.bottom + self._scaled_y(12),
            region_bottom - self._scaled_y(2),
        )
        if right <= left or bottom <= top:
            log.debug(
                "根据搜索占位词推导搜索框失败 "
                f"bounds={(left, top, right, bottom)!r}"
            )
            return None
        box = OCRTextBox(
            text="搜索框",
            score=placeholder.score,
            left=int(left),
            top=int(top),
            right=int(right),
            bottom=int(bottom),
        )
        log.debug(
            "已根据 OCR 识别到搜索框 "
            f"占位词={placeholder.text!r} box={(box.left, box.top, box.right, box.bottom)!r}"
        )
        return box

    def _candidate_search_points(self, image=None) -> list[tuple[int, int]]:
        if image is None:
            image = self.screenshot()
        region_left, region_top, region_right, region_bottom = self._search_region_box()
        mid_y = int((region_top + region_bottom) / 2)
        candidates: list[tuple[int, int]] = []
        source = "heuristic"
        search_box = self._find_search_input_box(image=image)
        if search_box is not None:
            source = "ocr:search-box"
            candidates.extend(
                [
                    (
                        int(search_box.left + search_box.width * 0.42),
                        int(search_box.top + search_box.height * 0.55),
                    ),
                    (
                        int(search_box.left + search_box.width * 0.58),
                        int(search_box.top + search_box.height * 0.55),
                    ),
                    (
                        int(search_box.left + search_box.width * 0.28),
                        int(search_box.top + search_box.height * 0.55),
                    ),
                ]
            )
        else:
            placeholder = self.find_search_placeholder(image=image)
            if placeholder is not None:
                source = "ocr:placeholder"
                cx, cy = placeholder.center
                candidates.extend(
                    [
                        (
                            region_left + min(cx, region_right - region_left - self._scaled_x(24)),
                            region_top + cy,
                        ),
                        (
                            region_left + min(cx + self._scaled_x(28), region_right - region_left - self._scaled_x(24)),
                            region_top + cy,
                        ),
                    ]
                )
            width = region_right - region_left
            candidates.extend(
                [
                    (region_left + int(width * 0.34), mid_y),
                    (region_left + int(width * 0.48), mid_y),
                    (region_left + int(width * 0.60), mid_y),
                ]
            )
        unique: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for point in candidates:
            bounded = (
                min(max(point[0], region_left + 12), region_right - 18),
                min(max(point[1], region_top + 8), region_bottom - 8),
            )
            if bounded in seen:
                continue
            seen.add(bounded)
            unique.append(bounded)
        log.debug(
            "搜索框候选点击点 "
            f"来源={source} 区域={(region_left, region_top, region_right, region_bottom)!r} 候选点={unique!r}"
        )
        return unique

    @staticmethod
    def _contains_any_prefix(text: str, prefixes: Iterable[str]) -> bool:
        normalized = VisualWeixinWindow._normalize_text(text)
        return any(prefix in normalized for prefix in prefixes)

    def find_search_placeholder(self, image=None) -> OCRTextBox | None:
        if image is None:
            image = self.screenshot()
        crop = self._search_region_image(image=image)
        region_left, region_top, _, _ = self._search_region_box()
        best: OCRTextBox | None = None
        for box in self.ocr_texts(image=crop):
            if "搜索" not in box.text:
                continue
            if best is None or box.score > best.score:
                best = box
        if best is not None:
            log.debug(
                "在搜索区域识别到占位词 "
                f"text={best.text!r} box={(best.left, best.top, best.right, best.bottom)!r}"
            )
            return best
        for box in self.ocr_texts(image=image):
            if "搜索" not in box.text:
                continue
            if box.left > int(self.rect.width * 0.45):
                continue
            if box.top > int(self.rect.height * 0.16):
                continue
            candidate = OCRTextBox(
                text=box.text,
                score=box.score,
                left=max(box.left - region_left, 0),
                top=max(box.top - region_top, 0),
                right=max(box.right - region_left, 0),
                bottom=max(box.bottom - region_top, 0),
            )
            log.debug(
                "在整窗左上区域识别到占位词 "
                f"text={box.text!r} box={(box.left, box.top, box.right, box.bottom)!r}"
            )
            return candidate
        return best

    def locate_search_input(self, image=None, prefer_cache: bool = True) -> tuple[int, int]:
        """Return a stable click point inside the search input.

        Works both when the input still shows the placeholder and when it
        already contains a previous query.
        """
        bounds = self._search_region_box()
        cached = self._get_cached_point_in_bounds("search.input", bounds)
        if prefer_cache and cached is not None:
            self._log_point_source("search.input", "cache", cached)
            return cached
        point = self._candidate_search_points(image=image)[0]
        self._set_cached_point("search.input", point)
        self._log_point_source("search.input", "ocr/heuristic", point)
        return point

    def find_global_search_network_entry(
        self,
        query: str,
        image=None,
    ) -> OCRTextBox | None:
        popup = VisualSearchPopup.find(timeout=0.6)
        if popup is None:
            return None
        return popup.find_network_entry(query=query, image=image)

    def find_global_search_session_entry(
        self,
        query: str,
        image=None,
        exact: bool = False,
    ) -> OCRTextBox | None:
        popup = VisualSearchPopup.find(timeout=0.6)
        if popup is None:
            return None
        return popup.find_session_entry(query=query, image=image, exact=exact)

    def switch_to_chat_tab(self, settle: float = 0.8) -> tuple[int, int]:
        image = self.screenshot()
        if self.find_search_placeholder(image=image) is not None:
            log.debug("搜索占位词已可见，跳过切换到聊天页")
            return (-1, -1)
        if self._find_search_input_box(image=image) is not None:
            log.debug("搜索框已可见，跳过切换到聊天页")
            return (-1, -1)
        rect = self.rect
        rel_x = int(rect.width * 0.035)
        rel_y = int(rect.height * 0.175)
        self._log_point_source("main.chat_tab", "heuristic", (rel_x, rel_y))
        return self.click(rel_x, rel_y, settle=settle)

    def _add_menu_button_bounds(self) -> tuple[int, int, int, int]:
        rect = self.rect
        return (
            int(rect.width * 0.17),
            int(rect.height * 0.01),
            int(rect.width * 0.30),
            int(rect.height * 0.11),
        )

    def find_add_menu_button(self, image=None) -> OCRTextBox | None:
        if image is None:
            image = self.screenshot()
        bounds = self._add_menu_button_bounds()
        crop = self._crop_box(bounds, image=image)
        best: OCRTextBox | None = None
        for box in self.ocr_texts(image=crop):
            normalized = self._normalize_text(box.text)
            if "+" not in normalized and "十" not in normalized and "⊕" not in normalized:
                continue
            candidate = OCRTextBox(
                text=box.text,
                score=box.score,
                left=bounds[0] + box.left,
                top=bounds[1] + box.top,
                right=bounds[0] + box.right,
                bottom=bounds[1] + box.bottom,
            )
            if best is None or candidate.score > best.score:
                best = candidate
        return best

    def locate_add_menu_button(self, image=None, prefer_cache: bool = True) -> tuple[int, int]:
        image = image or self.screenshot()
        cached = self._get_cached_point_in_bounds(
            "main.add_menu_button",
            self._add_menu_button_bounds(),
            pad_x=self._scaled_x(6),
            pad_y=self._scaled_y(6),
        )
        if prefer_cache and cached is not None:
            self._log_point_source("main.add_menu_button", "cache", cached)
            return cached
        button = self.find_add_menu_button(image=image)
        if button is not None:
            point = (
                int(button.left + button.width * 0.5),
                int(button.top + button.height * 0.5),
            )
            self._set_cached_point("main.add_menu_button", point)
            self._log_point_source("main.add_menu_button", "ocr", point, f"text={button.text!r}")
            return point
        if cached is not None:
            self._log_point_source("main.add_menu_button", "cache-fallback", cached)
            return cached
        bounds = self._add_menu_button_bounds()
        point = (
            int((bounds[0] + bounds[2]) / 2),
            int((bounds[1] + bounds[3]) / 2),
        )
        self._log_point_source("main.add_menu_button", "heuristic", point)
        self._set_cached_point("main.add_menu_button", point)
        return point

    def click_ocr_box(self, box: OCRTextBox, settle: float = 0.6) -> tuple[int, int]:
        cx, cy = box.center
        return self.click(cx, cy, settle=settle)

    def click_box_candidates(
        self,
        box: OCRTextBox,
        settle: float = 0.5,
        offsets: list[tuple[float, float]] | None = None,
    ) -> tuple[int, int]:
        if offsets is None:
            offsets = [(0.5, 0.5), (0.25, 0.5), (0.75, 0.5), (0.5, 0.35), (0.5, 0.65)]
        last = box.center
        for rx, ry in offsets:
            rel_x = int(box.left + box.width * rx)
            rel_y = int(box.top + box.height * ry)
            last = (rel_x, rel_y)
            self.click(rel_x, rel_y, settle=settle)
        return last

    def _save_debug_image(self, save_dir: str | Path | None, name: str) -> None:
        if not save_dir:
            return
        path = Path(save_dir)
        path.mkdir(parents=True, exist_ok=True)
        self.screenshot(path / name)

    @uilock
    def click(self, rel_x: int, rel_y: int, settle: float = 0.18) -> tuple[int, int]:
        abs_x, abs_y = self._resolve_abs_point(rel_x, rel_y)
        self._left_click_abs(abs_x, abs_y)
        time.sleep(settle)
        return abs_x, abs_y

    @uilock
    def double_click(self, rel_x: int, rel_y: int, settle: float = 0.2) -> tuple[int, int]:
        abs_x, abs_y = self._resolve_abs_point(rel_x, rel_y)
        self._left_click_abs(abs_x, abs_y)
        time.sleep(0.08)
        self._left_click_abs(abs_x, abs_y)
        time.sleep(settle)
        return abs_x, abs_y

    @uilock
    def select_all(self, settle: float = 0.2) -> None:
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord("A"), 0, 0, 0)
        win32api.keybd_event(ord("A"), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(settle)

    @uilock
    def backspace(self, settle: float = 0.15) -> None:
        win32api.keybd_event(win32con.VK_BACK, 0, 0, 0)
        win32api.keybd_event(win32con.VK_BACK, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(settle)

    @uilock
    def delete(self, settle: float = 0.15) -> None:
        win32api.keybd_event(win32con.VK_DELETE, 0, 0, 0)
        win32api.keybd_event(win32con.VK_DELETE, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(settle)

    @uilock
    def paste_text(self, text: str, settle: float = 0.18) -> None:
        SetClipboardText(text)
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord("V"), 0, 0, 0)
        win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(settle)

    @uilock
    def press_enter(self, settle: float = 0.25) -> None:
        win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
        win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(settle)

    @uilock
    def press_esc(self, settle: float = 0.12) -> None:
        win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
        win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(settle)

    @uilock
    def press_down(self, settle: float = 0.12) -> None:
        win32api.keybd_event(win32con.VK_DOWN, 0, 0, 0)
        win32api.keybd_event(win32con.VK_DOWN, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(settle)

    def _ensure_search_focus(
        self,
        debug_dir: str | Path | None = None,
        attempt: int = 0,
        prefer_cache: bool = True,
    ) -> tuple[int, int]:
        image = self.screenshot()
        if self._is_search_focused(image=image):
            point = self.locate_search_input(image=image, prefer_cache=prefer_cache)
            log.debug(
                f"搜索框已处于可输入状态 attempt={attempt} point={point}"
            )
            return point
        bounds = self._search_region_box()
        points: list[tuple[int, int]] = []
        cached = self._get_cached_point_in_bounds("search.input", bounds)
        if prefer_cache and cached is not None:
            points.append(cached)
        points.extend(self._candidate_search_points(image=image))
        deduped: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for point in points:
            if point in seen:
                continue
            seen.add(point)
            deduped.append(point)
        points = deduped
        last_point = points[0]
        for index, (rel_x, rel_y) in enumerate(points):
            last_point = (rel_x, rel_y)
            self.click(rel_x, rel_y, settle=0.15)
            focused = True
            log.debug(
                f"尝试聚焦搜索框 attempt={attempt} candidate={index} "
                f"point={last_point} focused={focused} action=单击后直接认为已聚焦"
            )
            self._save_debug_image(debug_dir, f"search-focus-attempt-{attempt}-{index}.png")
            if focused:
                self._set_cached_point("search.input", last_point)
                return last_point
        return last_point

    def _clear_search_text(
        self,
        focus_point: tuple[int, int],
        debug_dir: str | Path | None = None,
        attempt: int = 0,
    ) -> str:
        self.click(*focus_point, settle=0.1)
        self.select_all(settle=0.1)
        self.backspace(settle=0.1)
        self.delete(settle=0.1)
        self._save_debug_image(debug_dir, f"search-clear-shortcut-{attempt}.png")
        return self.read_search_text()

    def _set_search_text_fast(
        self,
        text: str,
        *,
        debug_dir: str | Path | None = None,
    ) -> bool:
        bounds = self._search_region_box()
        focus_point = self._get_cached_point_in_bounds(
            "search.input",
            bounds,
            pad_x=self._scaled_x(6),
            pad_y=self._scaled_y(6),
        )
        if focus_point is None:
            return False
        log.debug(
            "开始执行极速搜索写入 "
            f"目标文本={text!r} 聚焦点={focus_point}"
        )
        self.click(*focus_point, settle=0.05)
        self.select_all(settle=0.04)
        self.backspace(settle=0.04)
        self.delete(settle=0.04)
        self.paste_text(text, settle=0.08)
        self._save_debug_image(debug_dir, "search-fast-after-paste.png")
        return True

    def set_search_text(
        self,
        text: str,
        retries: int = 3,
        settle: float = 0.35,
        debug_dir: str | Path | None = None,
        prefer_cache: bool = True,
    ) -> bool:
        target = self._normalize_for_match(text)
        if not target:
            raise ValueError("text must not be empty")
        self.activate()
        for attempt in range(1, retries + 1):
            focus_point = self._ensure_search_focus(
                debug_dir=debug_dir,
                attempt=attempt,
                prefer_cache=prefer_cache,
            )
            before = self.read_search_text()
            log.debug(
                f"开始写入搜索内容 attempt={attempt} 当前文本={before!r} "
                f"目标文本={target!r} 聚焦点={focus_point}"
            )
            cleared = self._clear_search_text(focus_point, debug_dir=debug_dir, attempt=attempt)
            log.debug(
                f"清空搜索框后文本 attempt={attempt} value={cleared!r}"
            )
            self.select_all(settle=0.08)
            self.paste_text(text, settle=settle)
            self._save_debug_image(debug_dir, f"search-after-paste-{attempt}.png")
            actual = self.read_search_text()
            log.debug(
                f"写入搜索框后的实际文本 attempt={attempt} value={actual!r}"
            )
            normalized_actual = self._normalize_for_match(actual)
            if target and target in normalized_actual:
                log.debug(
                    f"搜索框文本已包含目标手机号 attempt={attempt} normalized_actual={normalized_actual!r}"
                )
                return True
            popup = VisualSearchPopup.find(timeout=0.2)
            if popup is not None and popup.find_network_entry(text) is not None:
                log.debug(f"已检测到网络查找入口 attempt={attempt}，停止继续重试搜索输入")
                return True
            time.sleep(0.2)
        return False

    def search_with_ocr(
        self,
        text: str,
        settle: float = 1.0,
        retries: int = 3,
        debug_dir: str | Path | None = None,
        submit: bool = True,
    ) -> bool:
        ok = self.set_search_text(text, retries=retries, settle=0.3, debug_dir=debug_dir)
        if not ok:
            return False
        if submit:
            self.press_enter(settle=settle)
            self._save_debug_image(debug_dir, "search-after-enter.png")
        return True

    def open_add_friend_from_global_search(
        self,
        phone: str,
        retries: int = 3,
        settle: float = 0.8,
        debug_dir: str | Path | None = None,
    ) -> bool:
        log.debug(
            "开始执行全局搜索添加好友 "
            f"手机号={phone!r} 重试次数={retries} "
            f"窗口模式=固定尺寸:{_FIXED_MAIN_WINDOW_WIDTH}x{_FIXED_MAIN_WINDOW_HEIGHT}"
        )
        self.activate()
        self.normalize_main_window()
        self.switch_to_chat_tab(settle=0.35)
        for attempt in range(1, retries + 1):
            search_ok = self.set_search_text(
                phone,
                retries=1,
                settle=0.25,
                debug_dir=debug_dir,
                prefer_cache=True,
            )
            current_text = self.read_search_text()
            log.debug(
                "搜索框写入完成 "
                f"attempt={attempt} mode=cache 成功={search_ok} 当前文本={current_text!r}"
            )
            if search_ok:
                popup = VisualSearchPopup.find(timeout=0.6)
                log.debug(
                    "检查全局搜索下拉结果 "
                    f"attempt={attempt} mode=cache 是否找到下拉窗={popup is not None}"
                )
                if popup is not None:
                    hit = popup.locate_network_entry(phone, prefer_cache=True)
                    if hit is not None:
                        log.debug(
                            "点击网络查找入口 "
                            f"attempt={attempt} mode=cache hit={hit}"
                        )
                        popup.click(*hit, settle=settle)
                        add_wnd = VisualAddFriendWindow.find(timeout=max(0.8, settle))
                        if add_wnd is not None:
                            return True
            search_ok = self.set_search_text(
                phone,
                retries=1,
                settle=0.25,
                debug_dir=debug_dir,
                prefer_cache=False,
            )
            current_text = self.read_search_text()
            log.debug(
                "搜索框写入完成 "
                f"attempt={attempt} mode=ocr 成功={search_ok} 当前文本={current_text!r}"
            )
            if not search_ok:
                time.sleep(0.25)
                continue
            popup = VisualSearchPopup.find(timeout=0.6)
            log.debug(
                "检查全局搜索下拉结果 "
                f"attempt={attempt} mode=ocr 是否找到下拉窗={popup is not None}"
            )
            popup_texts: list[str] = []
            entry = popup.find_network_entry(phone) if popup is not None else None
            if popup is not None:
                try:
                    popup_texts = [box.text for box in popup.ocr_texts()]
                except Exception as exc:
                    popup_texts = [f"<ocr-error:{exc}>"]
            self._save_debug_image(debug_dir, f"global-search-dropdown-{attempt}.png")
            if popup is not None and debug_dir:
                popup.screenshot(Path(debug_dir) / f"global-search-popup-{attempt}.png")
            log.debug(
                f"分析全局搜索结果 attempt={attempt} "
                f"目标入口={entry!r} 下拉文本={popup_texts!r}"
            )
            hit = popup.locate_network_entry(phone, prefer_cache=False) if popup is not None else None
            if hit is not None:
                entry_text = entry.text if entry is not None else "<cache>"
                log.debug(
                    "点击网络查找入口 "
                    f"attempt={attempt} mode=ocr text={entry_text!r} hit={hit}"
                )
                popup.click(*hit, settle=settle)
                add_wnd = VisualAddFriendWindow.find(timeout=max(0.8, settle))
                if add_wnd is not None:
                    return True
            add_wnd = VisualAddFriendWindow.find(timeout=0.2)
            log.debug(
                "检查是否已打开添加朋友窗口 "
                f"attempt={attempt} found={add_wnd is not None}"
            )
            if add_wnd is not None:
                return True
            time.sleep(0.25)
        log.debug("全局搜索打开添加朋友窗口失败，已耗尽重试次数")
        return False

    def open_add_friend_from_menu(
        self,
        phone: str,
        retries: int = 3,
        settle: float = 0.8,
        debug_dir: str | Path | None = None,
    ) -> bool:
        log.debug(
            "开始执行加号菜单添加好友 "
            f"手机号={phone!r} 重试次数={retries} "
            f"窗口模式=固定尺寸:{_FIXED_MAIN_WINDOW_WIDTH}x{_FIXED_MAIN_WINDOW_HEIGHT}"
        )
        self.activate()
        self.normalize_main_window()
        self.switch_to_chat_tab(settle=0.30)
        for attempt in range(1, retries + 1):
            image = self.screenshot()
            add_menu_point = self.locate_add_menu_button(image=image, prefer_cache=True)
            self.click(*add_menu_point, settle=max(0.35, settle))
            self._save_debug_image(debug_dir, f"add-menu-open-{attempt}-cache.png")
            popup = VisualAddMenuPopup.find(timeout=0.8)
            log.debug(
                "检查加号菜单弹窗 "
                f"attempt={attempt} mode=cache found={popup is not None}"
            )
            if popup is None:
                image = self.screenshot()
                add_menu_point = self.locate_add_menu_button(image=image, prefer_cache=False)
                self.click(*add_menu_point, settle=max(0.35, settle))
                self._save_debug_image(debug_dir, f"add-menu-open-{attempt}-ocr.png")
                popup = VisualAddMenuPopup.find(timeout=0.8)
                log.debug(
                    "检查加号菜单弹窗 "
                    f"attempt={attempt} mode=ocr found={popup is not None}"
                )
                if popup is None:
                    time.sleep(0.2)
                    continue
            add_friend_point = popup.locate_add_friend_entry(prefer_cache=True)
            if add_friend_point is None:
                time.sleep(0.2)
                continue
            popup.click(*add_friend_point, settle=max(0.4, settle))
            search_wnd = VisualAddFriendSearchWindow.find(timeout=max(1.2, settle + 0.6))
            log.debug(
                "检查加号菜单进入搜索添加朋友窗口 "
                f"attempt={attempt} mode=cache found={search_wnd is not None}"
            )
            if search_wnd is None:
                add_friend_point = popup.locate_add_friend_entry(prefer_cache=False)
                if add_friend_point is None:
                    time.sleep(0.2)
                    continue
                popup.click(*add_friend_point, settle=max(0.4, settle))
                search_wnd = VisualAddFriendSearchWindow.find(timeout=max(1.2, settle + 0.6))
                log.debug(
                    "检查加号菜单进入搜索添加朋友窗口 "
                    f"attempt={attempt} mode=ocr found={search_wnd is not None}"
                )
                if search_wnd is None:
                    time.sleep(0.2)
                    continue
            if search_wnd.search_phone(phone, retries=max(2, retries), settle=max(0.25, settle * 0.5)):
                return True
        log.debug("加号菜单打开添加朋友窗口失败，已耗尽重试次数")
        return False

    def _chat_input_region_box(self) -> tuple[int, int, int, int]:
        rect = self.rect
        left = int(rect.width * 0.34)
        top = int(rect.height * 0.82)
        right = int(rect.width * 0.93)
        bottom = int(rect.height * 0.96)
        return left, top, right, bottom

    def find_chat_input_box(self, image=None) -> OCRTextBox | None:
        if image is None:
            image = self.screenshot()
        left, top, right, bottom = self._chat_input_region_box()
        crop = self._crop_box((left, top, right, bottom), image=image)
        boxes = self.ocr_texts(image=crop)
        if boxes:
            union_left = min(box.left for box in boxes)
            union_top = min(box.top for box in boxes)
            union_right = max(box.right for box in boxes)
            union_bottom = max(box.bottom for box in boxes)
            box = OCRTextBox(
                text="聊天输入区",
                score=max(b.score for b in boxes),
                left=left + max(union_left - self._scaled_x(24), 0),
                top=top + max(union_top - self._scaled_y(26), 0),
                right=min(left + union_right + self._scaled_x(80), right - self._scaled_x(8)),
                bottom=min(top + union_bottom + self._scaled_y(40), bottom - self._scaled_y(6)),
            )
            log.debug(
                "VisualWeixinWindow.find_chat_input_box source=ocr:text-cluster "
                f"box={(box.left, box.top, box.right, box.bottom)!r}"
            )
            return box
        box = OCRTextBox(
            text="聊天输入区",
            score=0.0,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        )
        log.debug(
            "VisualWeixinWindow.find_chat_input_box source=heuristic "
            f"box={(box.left, box.top, box.right, box.bottom)!r}"
        )
        return box

    def locate_chat_input(self, image=None, prefer_cache: bool = True) -> tuple[int, int]:
        cached = self._get_cached_point_in_bounds(
            "chat.input",
            self._chat_input_region_box(),
            pad_x=self._scaled_x(8),
            pad_y=self._scaled_y(8),
        )
        if prefer_cache and cached is not None:
            self._log_point_source("chat.input", "cache", cached)
            return cached
        box = self.find_chat_input_box(image=image)
        point = (
            int(box.left + box.width * 0.18),
            int(box.top + box.height * 0.42),
        )
        self._set_cached_point("chat.input", point)
        self._log_point_source("chat.input", "ocr/heuristic", point)
        return point

    def send_message_to_current_session(
        self,
        msg: str,
        *,
        clear: bool = True,
        settle: float = 0.35,
        debug_dir: str | Path | None = None,
    ) -> str:
        self.activate()
        self.normalize_main_window()
        input_point = self.locate_chat_input(prefer_cache=True)
        self.click(*input_point, settle=0.08)
        self.double_click(*input_point, settle=0.08)
        if clear:
            self.select_all(settle=0.05)
            self.backspace(settle=0.05)
            self.delete(settle=0.05)
        self.paste_text(msg, settle=0.10)
        self.press_enter(settle=max(0.16, min(0.22, settle)))
        if debug_dir:
            self.screenshot(Path(debug_dir) / "send-msg-current-after-enter.png")
        return "sent"

    def send_message_to_session(
        self,
        who: str,
        msg: str,
        exact: bool = False,
        clear: bool = True,
        retries: int = 3,
        settle: float = 0.35,
        debug_dir: str | Path | None = None,
    ) -> str:
        log.debug(
            "VisualWeixinWindow.send_message_to_session "
            f"start who={who!r} exact={exact} window_mode=fixed:{_FIXED_MAIN_WINDOW_WIDTH}x{_FIXED_MAIN_WINDOW_HEIGHT}"
        )
        popup_timeout = min(0.45, max(0.25, settle + 0.1))
        miss_sleep = min(0.12, max(0.06, settle * 0.3))
        popup_click_settle = min(0.28, max(0.18, settle))
        post_click_settle = min(0.18, max(0.10, settle * 0.5))
        used_contact_cache = False
        has_contact_cache = VisualSearchPopup.has_cached_contact_entry(who)
        self.activate()
        self.normalize_main_window()
        if has_contact_cache:
            self.click(int(self.rect.width * 0.035), int(self.rect.height * 0.175), settle=0.10)
            search_ok = self._set_search_text_fast(who, debug_dir=debug_dir)
            if not search_ok:
                log.debug("极速搜索写入未命中缓存，回退标准搜索链路")
                self.switch_to_chat_tab(settle=0.18)
                search_ok = self.set_search_text(who, retries=retries, settle=0.16, debug_dir=debug_dir)
        else:
            self.switch_to_chat_tab(settle=0.18)
            search_ok = self.set_search_text(who, retries=retries, settle=0.16, debug_dir=debug_dir)
        if not search_ok:
            return "search_input_failed"
        for attempt in range(1, retries + 1):
            popup = VisualSearchPopup.find(timeout=popup_timeout)
            cached_hit = popup.locate_cached_contact_entry(who) if popup is not None else None
            if popup is not None and cached_hit is not None:
                log.debug(
                    "VisualWeixinWindow.send_message_to_session "
                    f"attempt={attempt} source=contact-cache action=enter point={cached_hit!r}"
                )
                used_contact_cache = True
                self.press_enter(settle=popup_click_settle)
                time.sleep(post_click_settle)
                if VisualSearchPopup.find(timeout=0.08) is None:
                    break
                log.debug(
                    "VisualWeixinWindow.send_message_to_session "
                    f"attempt={attempt} source=contact-cache result=popup-still-open fallback=ocr"
                )
                used_contact_cache = False
                popup.clear_cached_contact_entry(who)
            entry = popup.find_contact_entry(query=who, exact=exact) if popup is not None else None
            log.debug(
                "VisualWeixinWindow.send_message_to_session "
                f"attempt={attempt} popup_found={popup is not None} entry={entry!r}"
            )
            if popup is not None and debug_dir:
                popup.screenshot(Path(debug_dir) / f"send-msg-popup-{attempt}.png")
            if entry is not None:
                popup.remember_contact_entry(who, entry.center)
                popup.click_ocr_box(entry, settle=popup_click_settle)
                break
            time.sleep(miss_sleep)
        else:
            return "session_not_found"
        time.sleep(post_click_settle)
        input_point = self.locate_chat_input(prefer_cache=True)
        self.click(*input_point, settle=0.08)
        self.double_click(*input_point, settle=0.08)
        if clear:
            self.select_all(settle=0.05)
            self.backspace(settle=0.05)
            self.delete(settle=0.05)
        self.paste_text(msg, settle=0.10)
        self.press_enter(settle=max(0.16, min(0.22, settle)))
        if debug_dir:
            self.screenshot(Path(debug_dir) / "send-msg-after-enter.png")
        return "sent"


class VisualSearchPopup(VisualWeixinWindow):
    """Visual automation for the global-search suggestion popup."""

    WINDOW_CLASS = "Qt51514QWindowToolSaveBits"
    WINDOW_TITLE = "Weixin"
    _SECTION_HEADER_TEXTS = (
        "联系人",
        "群聊",
        "聊天记录",
        "公众号",
        "小程序",
        "服务号",
        "订阅号",
    )
    _NEGATIVE_SECTION_HEADERS = (
        "聊天记录",
        "公众号",
        "小程序",
        "服务号",
        "订阅号",
    )
    _POSITIVE_SECTION_HEADERS = (
        "联系人",
        "群聊",
    )

    def _contact_cache_step(self, query: str) -> str:
        normalized = self._normalize_for_match(query)
        return f"search.contact_entry:{normalized}"

    @classmethod
    def has_cached_contact_entry(cls, query: str) -> bool:
        normalized = cls._normalize_for_match(query)
        step = f"search.contact_entry:{normalized}"
        data = cls._load_visual_cache()
        for scope_key in (cls.__name__,):
            scope = data.get(scope_key, {})
            if step in scope:
                return True
        for scope_key, scope in data.items():
            if not isinstance(scope, dict):
                continue
            if not str(scope_key).startswith(f"{cls.__name__}:"):
                continue
            if step in scope:
                return True
        return False

    @classmethod
    def find(cls, timeout: float = 2.0) -> "VisualSearchPopup | None":
        return cls._find_window_by_title(
            window_class=cls.WINDOW_CLASS,
            title=cls.WINDOW_TITLE,
            timeout=timeout,
        )

    def find_network_entry(self, query: str, image=None) -> OCRTextBox | None:
        query_norm = self._normalize_for_match(query)
        prefixes = ("网络查找手机/QQ号", "网络查找手机号/QQ号", "网络查找手机/QQ", "网络查找")
        best: OCRTextBox | None = None
        for box in self.ocr_texts(image=image):
            normalized = self._normalize_text(box.text)
            if not normalized:
                continue
            if not self._contains_any_prefix(normalized, prefixes):
                continue
            if query_norm and query_norm not in self._normalize_for_match(normalized):
                continue
            if best is None or box.score > best.score:
                best = box
        return best

    def remember_contact_entry(self, query: str, point: tuple[int, int]) -> None:
        self._set_cached_point(self._contact_cache_step(query), point)

    def clear_cached_contact_entry(self, query: str) -> None:
        self._clear_cached_point(self._contact_cache_step(query))

    def locate_cached_contact_entry(self, query: str) -> tuple[int, int] | None:
        return self._get_cached_point_in_bounds(
            self._contact_cache_step(query),
            (0, 0, self.rect.width - 1, self.rect.height - 1),
            pad_x=self._scaled_x(8),
            pad_y=self._scaled_y(8),
        )

    def _find_section_header_box(
        self,
        header_text: str,
        *,
        boxes: Iterable[OCRTextBox],
    ) -> OCRTextBox | None:
        return self._find_text_box_in_boxes(header_text, boxes, exact=False)

    def _find_section_headers(
        self,
        boxes: list[OCRTextBox],
    ) -> list[tuple[str, OCRTextBox]]:
        headers: list[tuple[str, OCRTextBox]] = []
        for header_text in self._SECTION_HEADER_TEXTS:
            header = self._find_section_header_box(header_text, boxes=boxes)
            if header is not None:
                headers.append((header_text, header))
        headers.sort(key=lambda item: item[1].top)
        return headers

    def _contact_section_bounds(self, boxes: list[OCRTextBox]) -> tuple[int, int] | None:
        contact_header = self._find_section_header_box("联系人", boxes=boxes)
        if contact_header is None:
            return None
        next_top = self.rect.height
        for header_text, header in self._find_section_headers(boxes):
            if header_text == "联系人" or header.top <= contact_header.top:
                continue
            next_top = min(next_top, header.top)
        top = max(contact_header.bottom - self._scaled_y(4), 0)
        bottom = max(top, next_top - self._scaled_y(4))
        return top, bottom

    def _section_bounds(
        self,
        header_name: str,
        headers: list[tuple[str, OCRTextBox]],
    ) -> tuple[int, int] | None:
        current_header: OCRTextBox | None = None
        next_top = self.rect.height
        for current_name, header in headers:
            if current_name == header_name:
                current_header = header
                continue
            if current_header is not None and header.top > current_header.top:
                next_top = min(next_top, header.top)
                break
        if current_header is None:
            return None
        top = max(current_header.bottom - self._scaled_y(4), 0)
        bottom = max(top, next_top - self._scaled_y(4))
        return top, bottom

    def _section_name_for_box(
        self,
        box: OCRTextBox,
        headers: list[tuple[str, OCRTextBox]],
    ) -> str | None:
        current: str | None = None
        for header_text, header in headers:
            if box.top >= header.top:
                current = header_text
                continue
            break
        return current

    def find_contact_entry(
        self,
        query: str,
        image=None,
        exact: bool = False,
    ) -> OCRTextBox | None:
        image = image or self.screenshot()
        boxes = self.ocr_texts(image=image)
        headers = self._find_section_headers(boxes)
        allowed_bounds = {
            name: self._section_bounds(name, headers)
            for name in self._POSITIVE_SECTION_HEADERS
        }
        query_norm = self._normalize_for_match(query)
        best: OCRTextBox | None = None
        header_norms = {self._normalize_for_match(text) for text in self._SECTION_HEADER_TEXTS}
        for box in boxes:
            normalized = self._normalize_for_match(box.text)
            if not normalized:
                continue
            if normalized in header_norms:
                continue
            if self._contains_any_prefix(
                normalized,
                ("网络查找手机/QQ号", "网络查找手机号/QQ号", "网络查找手机/QQ", "网络查找"),
            ):
                continue
            matched = normalized == query_norm if exact else query_norm in normalized
            if not matched:
                continue
            section_name = self._section_name_for_box(box, headers)
            if section_name not in self._POSITIVE_SECTION_HEADERS:
                continue
            bounds = allowed_bounds.get(section_name)
            if bounds is None:
                continue
            top, bottom = bounds
            if box.bottom < top or box.top > bottom:
                continue
            if best is None or box.score > best.score:
                best = box
        if best is None:
            log.debug(
                "VisualSearchPopup.find_contact_entry "
                f"query={query!r} headers={[name for name, _ in headers]!r} allowed_bounds={allowed_bounds!r}"
            )
        return best

    def locate_network_entry(
        self,
        query: str,
        image=None,
        prefer_cache: bool = True,
    ) -> tuple[int, int] | None:
        image = image or self.screenshot()
        cached = self._get_cached_point_in_bounds(
            "search.network_entry",
            (0, 0, self.rect.width - 1, self.rect.height - 1),
            pad_x=self._scaled_x(4),
            pad_y=self._scaled_y(4),
        )
        if prefer_cache and cached is not None:
            self._log_point_source("search.network_entry", "cache", cached)
            return cached
        entry = self.find_network_entry(query, image=image)
        if entry is not None:
            point = (
                int(entry.left + entry.width * 0.5),
                int(entry.top + entry.height * 0.55),
            )
            self._set_cached_point("search.network_entry", point)
            self._log_point_source("search.network_entry", "ocr", point, f"text={entry.text!r}")
            return point
        if cached is not None:
            self._log_point_source("search.network_entry", "cache-fallback", cached)
            return cached
        return None

    def find_session_entry(
        self,
        query: str,
        image=None,
        exact: bool = False,
    ) -> OCRTextBox | None:
        query_norm = self._normalize_for_match(query)
        best: OCRTextBox | None = None
        for box in self.ocr_texts(image=image):
            normalized = self._normalize_for_match(box.text)
            if not normalized:
                continue
            if self._contains_any_prefix(
                normalized,
                ("网络查找手机/QQ号", "网络查找手机号/QQ号", "网络查找手机/QQ", "网络查找"),
            ):
                continue
            matched = normalized == query_norm if exact else query_norm in normalized
            if not matched:
                continue
            if best is None or box.score > best.score:
                best = box
        return best


class VisualAddMenuPopup(VisualWeixinWindow):
    """Operate the popup opened from the main window plus button."""

    WINDOW_CLASS = "Qt51514QWindowToolSaveBits"
    WINDOW_TITLE = "Weixin"

    @classmethod
    def find(cls, timeout: float = 1.5) -> "VisualAddMenuPopup | None":
        deadline = time.time() + timeout
        while time.time() < deadline:
            candidates = cls._visible_windows(window_class=cls.WINDOW_CLASS, title=cls.WINDOW_TITLE)
            for hwnd in candidates:
                try:
                    wnd = cls(hwnd)
                    image = wnd.screenshot()
                    if wnd.find_text_box("添加朋友", image=image, exact=False) is not None:
                        return wnd
                except Exception:
                    continue
            time.sleep(0.08)
        return None

    def locate_add_friend_entry(self, image=None, prefer_cache: bool = True) -> tuple[int, int] | None:
        image = image or self.screenshot()
        cached = self._get_cached_point_in_bounds(
            "menu.add_friend_entry",
            (0, 0, self.rect.width - 1, self.rect.height - 1),
            pad_x=self._scaled_x(6),
            pad_y=self._scaled_y(6),
        )
        if prefer_cache and cached is not None:
            self._log_point_source("menu.add_friend_entry", "cache", cached)
            return cached
        target = self.find_text_box("添加朋友", image=image, exact=False)
        if target is not None:
            point = (
                int(target.left + target.width * 0.35),
                int(target.top + target.height * 0.5),
            )
            self._set_cached_point("menu.add_friend_entry", point)
            self._log_point_source("menu.add_friend_entry", "ocr", point, f"text={target.text!r}")
            return point
        if cached is not None:
            self._log_point_source("menu.add_friend_entry", "cache-fallback", cached)
            return cached
        return None


class VisualAddFriendWindow(VisualWeixinWindow):
    """Operate the visible standalone '添加朋友' window by OCR and geometry."""

    TITLE_TEXT = "添加朋友"

    @classmethod
    def find(cls, timeout: float = 3.0) -> "VisualAddFriendWindow | None":
        return cls._find_window_by_title(
            window_class="Qt51514QWindowIcon",
            title=cls.TITLE_TEXT,
            timeout=timeout,
        )

    def _dialog_search_region_box(self) -> tuple[int, int, int, int]:
        rect = self.rect
        left = int(rect.width * 0.10)
        top = int(rect.height * 0.08)
        right = int(rect.width * 0.72)
        bottom = int(rect.height * 0.18)
        return left, top, right, bottom

    def _search_region_box(self) -> tuple[int, int, int, int]:
        return self._dialog_search_region_box()

    def find_add_to_contacts_button(self, image=None) -> OCRTextBox | None:
        return self.find_text_box("添加到通讯录", image=image, exact=False)

    def _inline_result_add_button_bounds(self) -> tuple[int, int, int, int]:
        rect = self.rect
        return (
            int(rect.width * 0.25),
            int(rect.height * 0.48),
            int(rect.width * 0.76),
            int(rect.height * 0.72),
        )

    def has_search_form_header(self, image=None) -> bool:
        image = image or self.screenshot()
        return (
            self.find_text_box("搜索", image=image, exact=False) is not None
            and (
                self.find_text_box("微信号", image=image, exact=False) is not None
                or self.find_text_box("手机号", image=image, exact=False) is not None
            )
        )

    def has_inline_search_result_layout(self, image=None) -> bool:
        image = image or self.screenshot()
        if not self.has_search_form_header(image=image):
            return False
        if self.has_not_found_marker(image=image):
            return False
        if self.find_text_box("添加到通讯录", image=image, exact=False) is not None:
            return True
        if self.find_text_box("发消息", image=image, exact=False) is not None:
            return True
        if self.find_text_box("发送消息", image=image, exact=False) is not None:
            return True
        if self.find_text_box("等待验证", image=image, exact=False) is not None:
            return True
        if self.find_text_box("账号异常", image=image, exact=False) is not None:
            return True
        if self.find_text_box("联系人较多", image=image, exact=False) is not None:
            return True
        if self.find_text_box("地区", image=image, exact=False) is not None:
            return True
        if self.find_text_box("昵称", image=image, exact=False) is not None:
            return True
        if self.find_text_box("没有昵称", image=image, exact=False) is not None:
            return True
        if self.find_text_box("添加朋友申请", image=image, exact=False) is not None:
            return True
        return True

    def _debug_ocr_summary(self, image=None, limit: int = 12) -> str:
        image = image or self.screenshot()
        texts: list[str] = []
        seen: set[str] = set()
        for box in self.ocr_texts(image=image):
            normalized = self._normalize_text(box.text)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            texts.append(normalized)
            if len(texts) >= limit:
                break
        return " | ".join(texts)

    def has_profile_like_layout(self, image=None) -> bool:
        image = image or self.screenshot()
        if self.has_not_found_marker(image=image):
            return False
        if self.has_search_form_header(image=image):
            return True
        profile_markers = (
            "添加到通讯录",
            "发消息",
            "发送消息",
            "等待验证",
            "地区",
            "昵称",
            "微信号",
            "手机号",
            "账号异常",
            "联系人较多",
        )
        hit_count = 0
        for marker in profile_markers:
            if self.find_text_box(marker, image=image, exact=False) is not None:
                hit_count += 1
            if hit_count >= 2:
                return True
        return False

    def _add_button_bounds(self) -> tuple[int, int, int, int]:
        rect = self.rect
        return (
            int(rect.width * 0.42),
            int(rect.height * 0.18),
            int(rect.width * 0.96),
            int(rect.height * 0.56),
        )

    def locate_add_to_contacts_button(self, image=None) -> tuple[int, int] | None:
        image = image or self.screenshot()
        button = self.find_add_to_contacts_button(image=image)
        if button is not None:
            point = (
                int(button.left + button.width * 0.44),
                int(button.top + button.height * 0.5),
            )
            self._set_cached_point("add_friend.add_to_contacts", point)
            self._log_point_source("add_friend.add_to_contacts", "ocr", point, f"text={button.text!r}")
            return point
        cached = self._get_cached_point_in_bounds(
            "add_friend.add_to_contacts",
            self._add_button_bounds(),
            pad_x=self._scaled_x(8),
            pad_y=self._scaled_y(8),
        )
        if cached is not None:
            self._log_point_source("add_friend.add_to_contacts", "cache", cached)
            return cached
        if self.has_inline_search_result_layout(image=image):
            bounds = self._inline_result_add_button_bounds()
            point = (
                int((bounds[0] + bounds[2]) / 2),
                int((bounds[1] + bounds[3]) / 2),
            )
            self._set_cached_point("add_friend.add_to_contacts", point)
            self._log_point_source("add_friend.add_to_contacts", "heuristic:inline-result", point)
            return point
        return None

    def _expand_button_box(
        self,
        text_box: OCRTextBox,
        pad_x: int = 26,
        pad_top: int = 10,
        pad_bottom: int = 18,
    ) -> OCRTextBox:
        rect = self.rect
        pad_x = self._scaled_x(pad_x)
        pad_top = self._scaled_y(pad_top)
        pad_bottom = self._scaled_y(pad_bottom)
        return OCRTextBox(
            text=text_box.text,
            score=text_box.score,
            left=max(text_box.left - pad_x, 0),
            top=max(text_box.top - pad_top, 0),
            right=min(text_box.right + pad_x, rect.width - 1),
            bottom=min(text_box.bottom + pad_bottom, rect.height - 1),
        )

    def _button_click_box_for_text(self, text_box: OCRTextBox) -> OCRTextBox:
        rect = self.rect
        # The clickable capsule is visibly wider and a bit taller than the OCR
        # text box. Bias downward slightly because the visual center of the
        # button is below the text baseline.
        pad_x = self._scaled_x(38)
        pad_top = self._scaled_y(8)
        pad_bottom = self._scaled_y(24)
        return OCRTextBox(
            text=text_box.text,
            score=text_box.score,
            left=max(text_box.left - pad_x, 0),
            top=max(text_box.top - pad_top, 0),
            right=min(text_box.right + pad_x, rect.width - 1),
            bottom=min(text_box.bottom + pad_bottom, rect.height - 1),
        )

    def find_send_button(self, image=None) -> OCRTextBox | None:
        button = self.find_text_box("发送", image=image, exact=False)
        if button is not None:
            return button
        return self.find_text_box("确定", image=image, exact=False)

    def find_pending_verification_button(self, image=None) -> OCRTextBox | None:
        return self.find_text_box("等待验证", image=image, exact=False)

    def has_add_button(self, image=None) -> bool:
        return self.find_add_to_contacts_button(image=image) is not None

    def has_send_message_button(self, image=None) -> bool:
        markers = ("发消息", "发送消息", "语音聊天", "视频聊天")
        return any(self.find_text_box(marker, image=image, exact=False) is not None for marker in markers)

    def has_pending_verification_button(self, image=None) -> bool:
        return self.find_pending_verification_button(image=image) is not None

    def has_not_found_marker(self, image=None) -> bool:
        markers = (
            "该用户不存在",
            "帐号不存在",
            "账号不存在",
            "用户不存在",
            "查无此人",
            "未找到",
            "没有找到",
        )
        return any(self.find_text_box(marker, image=image, exact=False) is not None for marker in markers)

    def detect_result_state(self, image=None) -> str:
        if image is None:
            image = self.screenshot()
        if self.has_add_button(image=image):
            return "found"
        if self.has_pending_verification_button(image=image):
            return "pending_verification"
        if self.has_send_message_button(image=image):
            return "already_friend"
        if self.has_not_found_marker(image=image):
            return "not_found"
        if self.has_inline_search_result_layout(image=image):
            return "found"
        if self.has_profile_like_layout(image=image):
            log.debug(
                "VisualAddFriendWindow.detect_result_state "
                f"fallback=profile-like-layout ocr={self._debug_ocr_summary(image=image)}"
            )
            return "found"
        log.debug(
            "VisualAddFriendWindow.detect_result_state "
            f"fallback=unknown ocr={self._debug_ocr_summary(image=image)}"
        )
        return "unknown"

    def click_add_to_contacts(self, settle: float = 0.8) -> bool:
        for attempt in range(2):
            image = self.screenshot()
            hit = self.locate_add_to_contacts_button(image=image)
            if hit is None:
                return False
            before = self.detect_result_state(image=image)
            self.click(*hit, settle=0.85)
            deadline = time.time() + max(settle, 2.0)
            while time.time() < deadline:
                if VisualFriendRequestWindow.find(timeout=0.1) is not None:
                    return True
                state = self.detect_result_state()
                if state != before:
                    return True
                time.sleep(0.15)
        return False

    def open_request_form(self, settle: float = 0.8) -> VisualFriendRequestWindow | None:
        return self.open_request_form_with_retry(retries=3, settle=settle)

    def open_request_form_with_retry(
        self,
        retries: int = 3,
        settle: float = 0.8,
        retry_interval: float = 1.2,
    ) -> "VisualFriendRequestWindow | None":
        for _ in range(max(1, retries)):
            if self.click_add_to_contacts(settle=settle):
                req = VisualFriendRequestWindow.find(timeout=max(1.5, settle + 0.8))
                if req is not None:
                    return req
            time.sleep(retry_interval)
        return None

    def detect_post_apply_state(self, timeout: float = 4.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            req = VisualFriendRequestWindow.find(timeout=0.2)
            if req is not None:
                return "request_form"
            image = self.screenshot()
            if self.has_pending_verification_button(image=image):
                return "applied"
            if self.find_text_box("发送添加朋友申请", image=image, exact=False) is not None:
                return "request_form"
            if self.find_send_button(image=image) is not None:
                return "send_form"
            if self.find_text_box("操作过于频繁", image=image, exact=False) is not None:
                return "rate_limited"
            if self.find_text_box("账号异常", image=image, exact=False) is not None:
                return "rate_limited"
            if self.find_text_box("联系人较多", image=image, exact=False) is not None:
                return "permission_required"
            time.sleep(0.2)
        return "unknown"

    def infer_post_submit_success(self, submit_ok: bool, timeout: float = 4.0) -> str:
        post_state = self.detect_post_apply_state(timeout=timeout)
        if post_state in {"rate_limited", "permission_required"}:
            return post_state
        if post_state == "applied":
            return post_state
        if submit_ok:
            log.debug(
                "VisualAddFriendWindow.infer_post_submit_success "
                f"submit_ok={submit_ok} post_state={post_state} -> optimistic-applied"
            )
            return "applied"
        return post_state

    def ensure_open(self) -> "VisualAddFriendWindow":
        image = self.screenshot()
        title = self.find_text_box(self.TITLE_TEXT, image=image, exact=False)
        if title is None:
            raise UiNotFoundError("未检测到添加朋友窗口标题")
        return self


class VisualAddFriendSearchWindow(VisualWeixinWindow):
    """Operate the standalone '添加朋友' search window opened from the plus menu."""

    TITLE_TEXT = "添加朋友"

    @classmethod
    def find(cls, timeout: float = 3.0) -> "VisualAddFriendSearchWindow | None":
        deadline = time.time() + timeout
        while time.time() < deadline:
            matches = cls._visible_windows(window_class="Qt51514QWindowIcon", title=cls.TITLE_TEXT)
            for hwnd in matches:
                try:
                    wnd = cls(hwnd)
                    image = wnd.screenshot()
                    if (
                        wnd.find_text_box("搜索", image=image, exact=False) is not None
                        and (
                            wnd.find_text_box("搜索微信号", image=image, exact=False) is not None
                            or wnd.find_text_box("手机号", image=image, exact=False) is not None
                        )
                    ):
                        return wnd
                except Exception:
                    continue
            time.sleep(0.08)
        return None

    def _search_input_region_box(self) -> tuple[int, int, int, int]:
        rect = self.rect
        return (
            int(rect.width * 0.04),
            int(rect.height * 0.06),
            int(rect.width * 0.70),
            int(rect.height * 0.19),
        )

    def _search_button_region_box(self) -> tuple[int, int, int, int]:
        rect = self.rect
        return (
            int(rect.width * 0.68),
            int(rect.height * 0.06),
            int(rect.width * 0.96),
            int(rect.height * 0.19),
        )

    def locate_search_input(self, image=None, prefer_cache: bool = True) -> tuple[int, int]:
        image = image or self.screenshot()
        cached = self._get_cached_point_in_bounds(
            "menu_search.input",
            self._search_input_region_box(),
            pad_x=self._scaled_x(6),
            pad_y=self._scaled_y(6),
        )
        if prefer_cache and cached is not None:
            self._log_point_source("menu_search.input", "cache", cached)
            return cached
        hints = ("搜索微信号", "搜索微信号或者手机号", "搜索微信号或手机号", "手机号")
        for hint in hints:
            box = self.find_text_box(hint, image=image, exact=False)
            if box is None:
                continue
            point = (
                min(int(box.left + box.width * 0.75), self._search_input_region_box()[2] - self._scaled_x(10)),
                int(box.top + box.height * 0.55),
            )
            self._set_cached_point("menu_search.input", point)
            self._log_point_source("menu_search.input", "ocr", point, f"text={box.text!r}")
            return point
        if cached is not None:
            self._log_point_source("menu_search.input", "cache-fallback", cached)
            return cached
        bounds = self._search_input_region_box()
        point = (int(bounds[0] + (bounds[2] - bounds[0]) * 0.45), int((bounds[1] + bounds[3]) / 2))
        self._set_cached_point("menu_search.input", point)
        self._log_point_source("menu_search.input", "heuristic", point)
        return point

    def locate_search_button(self, image=None, prefer_cache: bool = True) -> tuple[int, int] | None:
        image = image or self.screenshot()
        cached = self._get_cached_point_in_bounds(
            "menu_search.button",
            self._search_button_region_box(),
            pad_x=self._scaled_x(6),
            pad_y=self._scaled_y(6),
        )
        if prefer_cache and cached is not None:
            self._log_point_source("menu_search.button", "cache", cached)
            return cached
        button = self.find_text_box("搜索", image=image, exact=False)
        if button is not None:
            point = (
                int(button.left + button.width * 0.5),
                int(button.top + button.height * 0.55),
            )
            self._set_cached_point("menu_search.button", point)
            self._log_point_source("menu_search.button", "ocr", point, f"text={button.text!r}")
            return point
        if cached is not None:
            self._log_point_source("menu_search.button", "cache-fallback", cached)
            return cached
        return None

    def search_phone(self, phone: str, retries: int = 3, settle: float = 0.35) -> bool:
        target = self._normalize_for_match(phone)
        if not target:
            raise ValueError("phone must not be empty")
        for attempt in range(1, retries + 1):
            image = self.screenshot()
            input_point = self.locate_search_input(image=image, prefer_cache=True)
            self.click(*input_point, settle=0.08)
            self.click(*input_point, settle=0.06)
            self.select_all(settle=0.08)
            self.paste_text(phone, settle=settle)
            button_point = self.locate_search_button(prefer_cache=True)
            if button_point is None:
                return False
            self.click(*button_point, settle=max(0.35, settle))
            add_wnd = VisualAddFriendWindow.find(timeout=max(1.2, settle + 0.8))
            if self._is_search_result_ready(add_wnd, phone):
                return True
            self.press_enter(settle=max(0.35, settle))
            add_wnd = VisualAddFriendWindow.find(timeout=max(1.0, settle + 0.6))
            if self._is_search_result_ready(add_wnd, phone):
                return True
            image = self.screenshot()
            input_point = self.locate_search_input(image=image, prefer_cache=False)
            self.click(*input_point, settle=0.08)
            self.click(*input_point, settle=0.06)
            self.select_all(settle=0.08)
            self.paste_text(phone, settle=settle)
            button_point = self.locate_search_button(prefer_cache=False)
            if button_point is None:
                return False
            self.click(*button_point, settle=max(0.35, settle))
            add_wnd = VisualAddFriendWindow.find(timeout=max(1.2, settle + 0.8))
            if self._is_search_result_ready(add_wnd, phone):
                return True
            self.press_enter(settle=max(0.35, settle))
            add_wnd = VisualAddFriendWindow.find(timeout=max(1.0, settle + 0.6))
            if self._is_search_result_ready(add_wnd, phone):
                return True
            current = self.find_text_box(phone, image=self.screenshot(), exact=False)
            log.debug(
                "VisualAddFriendSearchWindow.search_phone "
                f"attempt={attempt} target={target!r} add_wnd={add_wnd is not None} input_seen={current is not None}"
            )
            time.sleep(0.2)
        return False

    def _is_search_result_ready(
        self,
        add_wnd: "VisualAddFriendWindow | None",
        phone: str,
    ) -> bool:
        if add_wnd is None:
            return False
        image = add_wnd.screenshot()
        if add_wnd.has_search_form_header(image=image):
            result_state = add_wnd.detect_result_state(image=image)
            log.debug(
                "VisualAddFriendSearchWindow._is_search_result_ready "
                f"header-still-visible phone={phone!r} result_state={result_state!r} "
                f"ocr={add_wnd._debug_ocr_summary(image=image)}"
            )
            return result_state != "unknown"
        return True


class VisualFriendRequestWindow(VisualWeixinWindow):
    """Operate the standalone '申请添加好友' dialog."""

    TITLE_TEXTS = ("申请添加好友", "申请添加朋友")

    @classmethod
    def find(cls, timeout: float = 3.0) -> "VisualFriendRequestWindow | None":
        title_deadline = min(timeout, 0.8)
        for title in cls.TITLE_TEXTS:
            by_title = cls._find_window_by_title(
                window_class="Qt51514QWindowIcon",
                title=title,
                timeout=title_deadline,
            )
            if by_title is not None:
                return by_title

        deadline = time.time() + max(0.1, timeout - title_deadline)
        required_sets = [
            ["申请添加好友", "确定"],
            ["申请添加朋友", "确定"],
            ["发送添加朋友申请", "备注", "朋友权限", "确定"],
        ]
        while time.time() < deadline:
            for hwnd in cls._visible_windows(window_class="Qt51514QWindowIcon"):
                try:
                    wnd = cls(hwnd)
                    image = wnd.screenshot()
                    texts = [cls._normalize_text(box.text) for box in wnd.ocr_texts(image=image)]
                    merged = " ".join(texts)
                    if any(all(req in merged for req in reqs) for reqs in required_sets):
                        return wnd
                except Exception:
                    continue
            time.sleep(0.1)
        return None

    def find_confirm_button(self, image=None) -> OCRTextBox | None:
        return self.find_text_box("确定", image=image, exact=False)

    def find_cancel_button(self, image=None) -> OCRTextBox | None:
        return self.find_text_box("取消", image=image, exact=False)

    def find_permission_option(
        self,
        target: str,
        image=None,
    ) -> OCRTextBox | None:
        return self.find_text_box(target, image=image, exact=False)

    def find_tag_option(self, tag: str, image=None) -> OCRTextBox | None:
        if image is None:
            image = self.screenshot()
        existing = self.find_text_box(tag, image=image, exact=False)
        if existing is not None and "创建新标签" not in existing.text:
            return existing
        for candidate in (
            f'创建新标签"{tag}"',
            f"创建新标签“{tag}”",
            f'创建新标签“{tag}"',
            f'创建新标签"{tag}”',
        ):
            found = self.find_text_box(candidate, image=image, exact=False)
            if found is not None:
                return found
        return existing

    def has_request_form(self, image=None) -> bool:
        image = image or self.screenshot()
        return (
            (
                self.find_text_box("申请添加好友", image=image, exact=False) is not None
                or self.find_text_box("申请添加朋友", image=image, exact=False) is not None
                or self.find_text_box("发送添加朋友申请", image=image, exact=False) is not None
            )
            and self.find_confirm_button(image=image) is not None
        )

    def _fallback_form_input_click_points(self) -> dict[str, tuple[int, int]]:
        rect = self.rect
        return {
            "verify_msg": (int(rect.width * 0.22), int(rect.height * 0.17)),
            "remark": (int(rect.width * 0.22), int(rect.height * 0.34)),
            "tags": (int(rect.width * 0.22), int(rect.height * 0.49)),
        }

    def _verify_input_bounds(self) -> tuple[int, int, int, int]:
        rect = self.rect
        return (
            int(rect.width * 0.08),
            int(rect.height * 0.10),
            int(rect.width * 0.92),
            int(rect.height * 0.30),
        )

    def _resolve_form_input_click_points(self, image=None) -> dict[str, tuple[int, int]]:
        image = image or self.screenshot()
        rect = self.rect
        points = self._fallback_form_input_click_points()
        cached_verify = self._get_cached_point_in_bounds(
            "request.verify_msg_input",
            self._verify_input_bounds(),
            pad_x=self._scaled_x(10),
            pad_y=self._scaled_y(10),
        )
        if cached_verify is not None:
            points["verify_msg"] = cached_verify
        label_specs = {
            "verify_msg": ("发送添加朋友申请", 22),
            "remark": ("备注", 26),
            "tags": ("标签", 26),
        }
        base_x = int(rect.width * 0.20)
        for key, (label_text, gap_y) in label_specs.items():
            label = self.find_text_box(label_text, image=image, exact=False)
            if label is None:
                continue
            rel_x = min(max(base_x, label.left + self._scaled_x(16)), rect.width - self._scaled_x(24))
            rel_y = min(max(label.bottom + self._scaled_y(gap_y), 0), rect.height - self._scaled_y(20))
            points[key] = (rel_x, rel_y)
            if key == "verify_msg":
                self._set_cached_point("request.verify_msg_input", points[key])
        log.debug(f"VisualFriendRequestWindow._resolve_form_input_click_points -> {points}")
        return points

    def _resolve_field_point_below_label(
        self,
        label_text: str,
        image=None,
        x_ratio: float = 0.12,
        gap_y: int = 40,
    ) -> tuple[int, int] | None:
        image = image or self.screenshot()
        rect = self.rect
        label = self.find_text_box(label_text, image=image, exact=False)
        if label is None:
            return None
        point = (
            int(rect.width * x_ratio),
            min(label.bottom + self._scaled_y(gap_y), rect.height - self._scaled_y(24)),
        )
        log.debug(
            "VisualFriendRequestWindow._resolve_field_point_below_label "
            f"label={label_text!r} point={point}"
        )
        return point

    def _fill_text_field(self, point: tuple[int, int], text: str, settle: float = 0.22) -> bool:
        if not text:
            return True
        self.click(point[0], point[1], settle=0.08)
        self.click(point[0], point[1], settle=0.06)
        self.select_all(settle=0.08)
        self.paste_text(text, settle=settle)
        return True

    def _focus_text_field(self, point: tuple[int, int]) -> None:
        self.click(point[0], point[1], settle=0.08)
        self.click(point[0], point[1], settle=0.06)

    def _resolve_remark_point(self, image=None) -> tuple[int, int]:
        image = image or self.screenshot()
        cached = self._get_cached_point("request.remark_input")
        if cached is not None:
            return cached
        by_label = self._resolve_field_point_below_label("备注", image=image, x_ratio=0.22, gap_y=42)
        if by_label is not None:
            self._log_point_source("request.remark_input", "ocr:label", by_label, "label=备注")
            self._set_cached_point("request.remark_input", by_label)
            return by_label
        fallback = self._resolve_form_input_click_points(image=image)["remark"]
        self._log_point_source("request.remark_input", "heuristic", fallback)
        self._set_cached_point("request.remark_input", fallback)
        return fallback

    def _resolve_tags_point(self, image=None) -> tuple[int, int]:
        image = image or self.screenshot()
        cached = self._get_cached_point("request.tags_input")
        if cached is not None:
            return cached
        by_label = self._resolve_field_point_below_label("标签", image=image, x_ratio=0.12, gap_y=42)
        if by_label is not None:
            self._log_point_source("request.tags_input", "ocr:label", by_label, "label=标签")
            self._set_cached_point("request.tags_input", by_label)
            return by_label
        fallback = self._resolve_form_input_click_points(image=image)["tags"]
        self._log_point_source("request.tags_input", "heuristic", fallback)
        self._set_cached_point("request.tags_input", fallback)
        return fallback

    def _confirm_tag_option_by_keyboard(self, tag: str) -> bool:
        log.debug(
            "VisualFriendRequestWindow._confirm_tag_option_by_keyboard "
            f"tag={tag!r} action='down+enter'"
        )
        self.press_down(settle=0.25)
        self.press_enter(settle=0.25)
        return True

    def set_verify_message(self, text: str, point: tuple[int, int] | None = None) -> bool:
        log.debug(
            "VisualFriendRequestWindow.set_verify_message "
            f"enabled={bool(text)}"
        )
        points = self._fallback_form_input_click_points()
        target = point or points["verify_msg"]
        ok = self._fill_text_field(target, text)
        if ok:
            self._set_cached_point("request.verify_msg_input", target)
        else:
            self._clear_cached_point("request.verify_msg_input")
        return ok

    def set_remark(self, text: str, point: tuple[int, int] | None = None) -> bool:
        log.debug(
            "VisualFriendRequestWindow.set_remark "
            f"enabled={bool(text)}"
        )
        points = self._fallback_form_input_click_points()
        target = point or points["remark"]
        ok = self._fill_text_field(target, text)
        if ok:
            self._set_cached_point("request.remark_input", target)
        else:
            self._clear_cached_point("request.remark_input")
        return ok

    def set_tags(self, text: str, point: tuple[int, int] | None = None) -> bool:
        log.debug(
            "VisualFriendRequestWindow.set_tags "
            f"enabled={bool(text)} value={text!r}"
        )
        points = self._fallback_form_input_click_points()
        target_point = point or points["tags"]
        if not text:
            return True
        self._focus_text_field(target_point)
        self._set_cached_point("request.tags_input", target_point)
        self.select_all(settle=0.06)
        self.paste_text(text, settle=0.16)
        return self._confirm_tag_option_by_keyboard(text)

    def set_multiple_tags(
        self,
        tags: list[str],
        point: tuple[int, int] | None = None,
    ) -> bool:
        clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        log.debug(
            "VisualFriendRequestWindow.set_multiple_tags "
            f"count={len(clean_tags)} values={clean_tags!r}"
        )
        if not clean_tags:
            return True
        for index, tag in enumerate(clean_tags):
            if not self.set_tags(tag, point=point):
                self._clear_cached_point("request.tags_input")
                return False
            if index + 1 < len(clean_tags):
                self.click(*(point or self._fallback_form_input_click_points()["tags"]), settle=0.10)
            else:
                log.debug("VisualFriendRequestWindow.set_multiple_tags finalize with esc")
                self.press_esc(settle=0.10)
        return True

    def fill_form(
        self,
        verify_msg: str = "",
        remark: str = "",
        tags: str | list[str] = "",
        permission: str = "聊天、朋友圈、微信运动等",
    ) -> bool:
        image = self.screenshot()
        points = self._resolve_form_input_click_points(image=image)
        verify_ok = self.set_verify_message(verify_msg, point=points["verify_msg"])
        remark_ok = self.set_remark(remark, point=self._resolve_remark_point(image=image))
        tags_ok = True
        if tags:
            tags_point = self._resolve_tags_point(image=image)
            if isinstance(tags, list):
                tags_ok = self.set_multiple_tags(tags, point=tags_point)
            else:
                tags_ok = self.set_tags(tags, point=tags_point)
        permission_ok = self.ensure_permission(permission, settle=0.25)
        log.debug(
            "VisualFriendRequestWindow.fill_form "
            f"verify_ok={verify_ok} remark_ok={remark_ok} "
            f"tags_ok={tags_ok} permission_ok={permission_ok}"
        )
        return bool(verify_ok and remark_ok and tags_ok and permission_ok)

    def ensure_permission(
        self,
        permission: str = "聊天、朋友圈、微信运动等",
        settle: float = 0.8,
    ) -> bool:
        image = self.screenshot()
        target = self.find_permission_option(permission, image=image)
        cached = self._get_cached_point_in_bounds(
            f"request.permission:{permission}",
            (0, int(self.rect.height * 0.45), self.rect.width - 1, int(self.rect.height * 0.90)),
            pad_x=self._scaled_x(12),
            pad_y=self._scaled_y(12),
        )
        log.debug(
            "VisualFriendRequestWindow.ensure_permission "
            f"permission={permission!r} found={target is not None}"
        )
        if target is None and cached is None:
            return False
        if target is not None:
            row_hit = OCRTextBox(
                text=target.text,
                score=target.score,
                left=max(target.left - self._scaled_x(24), 0),
                top=max(target.top - self._scaled_y(10), 0),
                right=min(target.right + self._scaled_x(80), self.rect.width - 1),
                bottom=min(target.bottom + self._scaled_y(14), self.rect.height - 1),
            )
            point = (
                int(row_hit.left + row_hit.width * 0.35),
                int(row_hit.top + row_hit.height * 0.55),
            )
            self._set_cached_point(f"request.permission:{permission}", point)
        else:
            point = cached
        self.click(point[0], point[1], settle=settle)
        return True

    def click_confirm(self, settle: float = 0.35) -> bool:
        image = self.screenshot()
        btn = self.find_confirm_button(image=image)
        cached = self._get_cached_point_in_bounds(
            "request.confirm_button",
            (int(self.rect.width * 0.52), int(self.rect.height * 0.78), self.rect.width - 1, self.rect.height - 1),
            pad_x=self._scaled_x(12),
            pad_y=self._scaled_y(12),
        )
        if btn is None and cached is None:
            log.debug("VisualFriendRequestWindow.click_confirm confirm button not found")
            return False
        if btn is not None:
            hit = OCRTextBox(
                text=btn.text,
                score=btn.score,
                left=max(btn.left - self._scaled_x(24), 0),
                top=max(btn.top - self._scaled_y(12), 0),
                right=min(btn.right + self._scaled_x(24), self.rect.width - 1),
                bottom=min(btn.bottom + self._scaled_y(18), self.rect.height - 1),
            )
            point = (
                int(hit.left + hit.width * 0.5),
                int(hit.top + hit.height * 0.55),
            )
            self._set_cached_point("request.confirm_button", point)
        else:
            point = cached
        self.click(point[0], point[1], settle=0.14)
        deadline = time.time() + max(settle, 0.7)
        while time.time() < deadline:
            if VisualFriendRequestWindow.find(timeout=0.1) is None:
                log.debug("VisualFriendRequestWindow.click_confirm dialog closed after click")
                return True
            time.sleep(0.08)
        # Do not treat a still-visible dialog within this short window as an
        # immediate click failure. The caller will perform a stronger
        # post-submit classification next.
        log.debug(
            "VisualFriendRequestWindow.click_confirm click sent but dialog still visible; "
            "defer final classification to post-submit checks"
        )
        return True

    def submit(self, settle: float = 1.0) -> bool:
        return self.click_confirm(settle=settle)

    def apply_form(
        self,
        verify_msg: str = "",
        remark: str = "",
        tags: str | list[str] = "",
        permission: str = "聊天、朋友圈、微信运动等",
        settle: float = 1.0,
    ) -> bool:
        filled_ok = self.fill_form(
            verify_msg=verify_msg,
            remark=remark,
            tags=tags,
            permission=permission,
        )
        if not filled_ok:
            return False
        submit_ok = self.submit(settle=settle)
        log.debug(
            "VisualFriendRequestWindow.apply_form "
            f"submit_ok={submit_ok}"
        )
        return submit_ok


__all__ = [
    "OCRTextBox",
    "VisualAddFriendSearchWindow",
    "VisualAddMenuPopup",
    "VisualAddFriendWindow",
    "VisualFriendRequestWindow",
    "VisualSearchPopup",
    "VisualWeixinWindow",
    "WindowRect",
]
