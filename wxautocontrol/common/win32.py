from __future__ import annotations

import ctypes

from PIL import Image
import pyperclip
import win32api
import win32clipboard
import win32con
import win32gui
import win32process
import win32ui
import psutil


def SetClipboardText(text: str):
    pyperclip.copy(text)


def capture(hwnd, bbox):
    win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(hwnd)
    win_width = win_right - win_left
    win_height = win_bottom - win_top

    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()

    saveBitMap = win32ui.CreateBitmap()
    saveBitMap.CreateCompatibleBitmap(mfcDC, win_width, win_height)
    saveDC.SelectObject(saveBitMap)

    ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 3)

    bmpinfo = saveBitMap.GetInfo()
    bmpstr = saveBitMap.GetBitmapBits(True)
    im = Image.frombuffer(
        "RGB",
        (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
        bmpstr,
        "raw",
        "BGRX",
        0,
        1,
    )

    win32gui.DeleteObject(saveBitMap.GetHandle())
    saveDC.DeleteDC()
    mfcDC.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwndDC)

    bbox_left, bbox_top, bbox_right, bbox_bottom = bbox
    crop_left = bbox_left - win_left
    crop_top = bbox_top - win_top
    crop_right = bbox_right - win_left
    crop_bottom = bbox_bottom - win_top
    return im.crop((crop_left, crop_top, crop_right, crop_bottom))


def get_path_by_hwnd(hwnd):
    try:
        _, process_id = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(process_id).exe()
    except Exception:
        return None
