"""HTTP endpoint handlers for Sala de Reuniao."""

from __future__ import annotations

import csv
import io
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import Body, Depends, Header, HTTPException

from backend.schemas import (
    SalaReuniaoCriarSalaRequest,
    SalaReuniaoUsoAdicionarRequest,
    SalaReuniaoEncerrarLocalRequest,
    SalaReuniaoEncerrarTodasRequest,
)
from backend.services import sala_reuniao_context as sala_context
from backend.services.sala_reuniao_daily import *
from backend.services.sala_reuniao_store import *
from backend.services.sala_reuniao_core import *


def _sala_reuniao_rustdesk_candidate_paths() -> list[Path]:
    paths: list[Path] = []
    for env_name in ("JK_RUSTDESK_PATH", "RUSTDESK_PATH"):
        raw = str(os.getenv(env_name) or "").strip().strip('"')
        if raw:
            paths.append(Path(raw).expanduser())

    appdata = Path(os.getenv("APPDATA") or "")
    localappdata = Path(os.getenv("LOCALAPPDATA") or "")
    program_files = Path(os.getenv("ProgramFiles") or "")
    program_files_x86 = Path(os.getenv("ProgramFiles(x86)") or "")
    userprofile = Path(os.getenv("USERPROFILE") or "")

    paths.extend([
        Path.cwd() / "rustdesk" / "rustdesk.exe",
        Path.cwd() / "tools" / "rustdesk" / "rustdesk.exe",
        Path.cwd() / "bin" / "rustdesk.exe",
        appdata / "JK Sistema Cliente" / "local_app" / "rustdesk" / "rustdesk.exe",
        appdata / "JK Sistema Cliente" / "local_app" / "tools" / "rustdesk" / "rustdesk.exe",
        appdata / "JK Sistema Cliente" / "local_app" / "bin" / "rustdesk.exe",
        program_files / "RustDesk" / "rustdesk.exe",
        program_files_x86 / "RustDesk" / "rustdesk.exe",
        localappdata / "Programs" / "RustDesk" / "rustdesk.exe",
        localappdata / "RustDesk" / "rustdesk.exe",
        userprofile / "Downloads" / "rustdesk.exe",
        userprofile / "Desktop" / "rustdesk.exe",
    ])

    if platform.system().lower() == "darwin":
        paths.extend([
            Path("/Applications/RustDesk.app/Contents/MacOS/RustDesk"),
            Path.home() / "Applications" / "RustDesk.app" / "Contents" / "MacOS" / "RustDesk",
        ])

    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        text = str(path)
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(path)
    return unique


def _sala_reuniao_rustdesk_executable() -> str:
    for path in _sala_reuniao_rustdesk_candidate_paths():
        try:
            if path.is_file():
                return str(path)
        except Exception:
            continue
    for name in ("rustdesk.exe", "rustdesk"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _sala_reuniao_rustdesk_running() -> bool:
    system = platform.system().lower()
    try:
        if system == "windows":
            output = subprocess.check_output(
                ["tasklist.exe", "/FI", "IMAGENAME eq rustdesk.exe", "/FO", "CSV", "/NH"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=2,
            )
            return "rustdesk.exe" in output.lower()
        if system == "darwin":
            result = subprocess.run(["pgrep", "-ix", "RustDesk"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            return result.returncode == 0
        result = subprocess.run(["pgrep", "-ix", "rustdesk"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return result.returncode == 0
    except Exception:
        return False


def _sala_reuniao_rustdesk_launch_command(executable: str) -> tuple[list[str], Optional[str]]:
    system = platform.system().lower()
    if system == "darwin" and not executable:
        return ["open", "-a", "RustDesk"], None
    if system == "darwin" and executable.endswith(".app"):
        return ["open", executable], None
    if not executable:
        return [], None
    return [executable], str(Path(executable).parent)


def _sala_reuniao_rustdesk_start_process(command: list[str], cwd: Optional[str]) -> subprocess.Popen:
    creationflags = 0
    if platform.system().lower() == "windows":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    return subprocess.Popen(
        command,
        cwd=cwd or None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )


def _sala_reuniao_rustdesk_terminate_pids(pids: set[int]) -> dict[str, Any]:
    if platform.system().lower() != "windows" or not pids:
        return {"terminated": [], "errors": []}
    terminated: list[int] = []
    errors: list[str] = []
    current_pid = os.getpid()
    for pid in sorted({int(pid) for pid in pids if int(pid or 0) > 0 and int(pid or 0) != current_pid}):
        try:
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/F", "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4,
            )
            if result.returncode == 0:
                terminated.append(pid)
            else:
                errors.append(f"{pid}: taskkill retornou {result.returncode}")
        except Exception as exc:
            errors.append(f"{pid}: {exc}")
    if terminated:
        time.sleep(1.0)
    return {"terminated": terminated, "errors": errors}


def _sala_reuniao_rustdesk_window_process_ids() -> set[int]:
    if platform.system().lower() != "windows":
        return set()
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rustdesk_pids = _sala_reuniao_rustdesk_process_ids()
    if not rustdesk_pids:
        return set()
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    window_pids: set[int] = set()

    def collect(hwnd) -> None:
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_value = int(pid.value)
            if pid_value not in rustdesk_pids:
                return
            info = _sala_reuniao_window_info(user32, int(hwnd))
            class_name = str(info.get("class_name") or "").lower()
            title = str(info.get("title") or "").lower()
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            if width >= 120 and height >= 90 and ("rustdesk" in title or "flutter" in class_name):
                window_pids.add(pid_value)
        except Exception:
            return

    def child_callback(child_hwnd, _lparam):
        collect(child_hwnd)
        return True

    def callback(hwnd, _lparam):
        collect(hwnd)
        try:
            user32.EnumChildWindows(hwnd, enum_proc(child_callback), 0)
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return window_pids


def _sala_reuniao_rustdesk_stale_child_pids(parent_hwnd: int) -> set[int]:
    if platform.system().lower() != "windows" or not parent_hwnd:
        return set()
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rustdesk_pids = _sala_reuniao_rustdesk_process_ids()
    if not rustdesk_pids:
        return set()
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    windows_by_pid: dict[int, list[dict[str, Any]]] = {}

    def collect(hwnd) -> None:
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_value = int(pid.value)
            if pid_value not in rustdesk_pids:
                return
            info = _sala_reuniao_window_info(user32, int(hwnd))
            info["pid"] = pid_value
            attached_to_app = (
                int(info.get("parent") or 0) == int(parent_hwnd)
                or int(info.get("root") or 0) == int(parent_hwnd)
                or _sala_reuniao_window_belongs_to_parent(user32, int(hwnd), int(parent_hwnd))
            )
            eval_info = _sala_reuniao_rustdesk_window_eval(info)
            class_name = str(info.get("class_name") or "").strip().lower()
            corrupted = (
                attached_to_app
                and (
                    bool(eval_info.get("rejected_class"))
                    or bool(eval_info.get("offscreen"))
                    or bool(eval_info.get("too_small"))
                    or not bool(info.get("visible"))
                )
            ) or (
                class_name in _RUSTDESK_REJECTED_WINDOW_CLASSES
                and (bool(eval_info.get("offscreen")) or int(info.get("parent") or 0) != 0)
            )
            if corrupted or eval_info.get("valid"):
                info["_stale_candidate"] = bool(corrupted)
                info["_valid_rustdesk_ui"] = bool(eval_info.get("valid"))
                windows_by_pid.setdefault(pid_value, []).append(info)
        except Exception:
            return

    def child_callback(child_hwnd, _lparam):
        collect(child_hwnd)
        return True

    def callback(hwnd, _lparam):
        collect(hwnd)
        try:
            user32.EnumChildWindows(hwnd, enum_proc(child_callback), 0)
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    stale_pids: set[int] = set()
    for pid_value, windows in windows_by_pid.items():
        has_valid_ui = any(bool(item.get("_valid_rustdesk_ui")) for item in windows)
        has_stale = any(bool(item.get("_stale_candidate")) for item in windows)
        if has_stale and not has_valid_ui:
            stale_pids.add(pid_value)
    return stale_pids


def _sala_reuniao_rustdesk_process_ids() -> set[int]:
    if platform.system().lower() != "windows":
        return set()
    try:
        output = subprocess.check_output(
            ["tasklist.exe", "/FI", "IMAGENAME eq rustdesk.exe", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=2,
        )
    except Exception:
        return set()
    pids: set[int] = set()
    for row in csv.reader(io.StringIO(output)):
        if len(row) >= 2 and str(row[0]).strip('"').lower() == "rustdesk.exe":
            try:
                pids.add(int(str(row[1]).strip()))
            except Exception:
                pass
    return pids


def _sala_reuniao_win_int(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)
    except Exception:
        return 0


def _sala_reuniao_rustdesk_bounds(payload: Optional[dict[str, Any]]) -> dict[str, int]:
    payload = payload if isinstance(payload, dict) else {}
    raw = payload.get("bounds") if isinstance(payload.get("bounds"), dict) else {}
    try:
        scale = float(payload.get("device_pixel_ratio") or payload.get("scale") or 1)
    except Exception:
        scale = 1
    scale = max(0.5, min(scale, 4))

    def number(*keys: str, default: float = 0) -> float:
        for key in keys:
            try:
                value = raw.get(key)
                if value is not None and str(value).strip() != "":
                    return float(value)
            except Exception:
                pass
        return default

    x = int(round(max(0, number("x", "left")) * scale))
    y = int(round(max(0, number("y", "top")) * scale))
    width = int(round(max(320, number("width", "w", default=900)) * scale))
    height = int(round(max(240, number("height", "h", default=560)) * scale))
    return {"x": x, "y": y, "width": width, "height": height}


def _sala_reuniao_window_info(user32: Any, hwnd: int) -> dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    class_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
    length = user32.GetWindowTextLengthW(hwnd)
    text_buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, text_buffer, len(text_buffer))
    owner = int(user32.GetWindow(hwnd, 4) or 0)  # GW_OWNER
    root = int(user32.GetAncestor(hwnd, 2) or 0)  # GA_ROOT
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    return {
        "hwnd": int(hwnd),
        "class_name": str(class_buffer.value or ""),
        "title": str(text_buffer.value or ""),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "enabled": bool(user32.IsWindowEnabled(hwnd)),
        "parent": int(user32.GetParent(hwnd) or 0),
        "owner": owner,
        "root": root,
        "is_top_level": int(user32.GetParent(hwnd) or 0) == 0,
        "x": int(rect.left),
        "y": int(rect.top),
        "width": width,
        "height": height,
        "area": max(0, width) * max(0, height),
    }


_RUSTDESK_REAL_UI_CLASSES = {
    "rustdeskmultiwindow",
    "flutter_runner_win32_window",
    "flutterview",
}

_RUSTDESK_REJECTED_WINDOW_CLASSES = {
    "consolewindowclass",
    "ime",
    "msctfime ui",
    "tao thread event target",
}


def _sala_reuniao_rustdesk_window_eval(info: dict[str, Any]) -> dict[str, Any]:
    class_name = str(info.get("class_name") or "").strip().lower()
    title = str(info.get("title") or "").strip().lower()
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    x = int(info.get("x") or 0)
    y = int(info.get("y") or 0)
    offscreen = x <= -20000 or y <= -20000
    too_small = width < 180 or height < 140
    rejected_class = class_name in _RUSTDESK_REJECTED_WINDOW_CLASSES
    real_class = class_name in _RUSTDESK_REAL_UI_CLASSES
    real_title = "rustdesk" in title or "remote desktop" in title
    valid = bool((real_class or real_title) and not rejected_class and not offscreen and not too_small)
    reason = ""
    if rejected_class:
        reason = "rejected_class"
    elif offscreen:
        reason = "offscreen"
    elif too_small:
        reason = "too_small"
    elif not (real_class or real_title):
        reason = "not_rustdesk_ui"
    return {
        "valid": valid,
        "reason": reason,
        "real_class": real_class,
        "real_title": real_title,
        "rejected_class": rejected_class,
        "offscreen": offscreen,
        "too_small": too_small,
    }


def _sala_reuniao_rustdesk_window_summary(info: dict[str, Any], reason: str = "") -> dict[str, Any]:
    return {
        "hwnd": str(info.get("hwnd") or ""),
        "pid": int(info.get("pid") or 0),
        "title": str(info.get("title") or ""),
        "class_name": str(info.get("class_name") or ""),
        "visible": bool(info.get("visible")),
        "parent": str(info.get("parent") or 0),
        "root": str(info.get("root") or 0),
        "x": int(info.get("x") or 0),
        "y": int(info.get("y") or 0),
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
        "reason": reason,
    }


def _sala_reuniao_prepare_dock_parent_window(user32: Any, hwnd: int) -> None:
    if platform.system().lower() != "windows" or not hwnd:
        return
    import ctypes
    from ctypes import wintypes

    SW_RESTORE = 9
    SW_MAXIMIZE = 3
    SW_SHOW = 5
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.ShowWindow(hwnd, SW_SHOW)
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        offscreen = int(rect.left) <= -20000 or int(rect.top) <= -20000
        too_small = width < 320 or height < 240
        if offscreen or too_small:
            user32.SetWindowPos(
                hwnd,
                0,
                80,
                80,
                max(1024, width if width > 0 else 1280),
                max(720, height if height > 0 else 800),
                SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
            time.sleep(0.2)
            user32.ShowWindow(hwnd, SW_SHOW)
    except Exception:
        return


def _sala_reuniao_resolve_dock_parent(parent_hwnd: int) -> dict[str, Any]:
    if platform.system().lower() != "windows" or not parent_hwnd:
        return {"hwnd": int(parent_hwnd or 0), "resolved": False}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        if not user32.IsWindow(parent_hwnd):
            return {"hwnd": int(parent_hwnd), "resolved": False, "reason": "parent_not_window"}
        _sala_reuniao_prepare_dock_parent_window(user32, parent_hwnd)
        parent_info = _sala_reuniao_window_info(user32, parent_hwnd)
        if parent_info.get("class_name") == "Chrome_RenderWidgetHostHWND":
            return {"hwnd": int(parent_hwnd), "resolved": False, "parent": parent_info, "reason": "already_render_widget"}

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        children: list[dict[str, Any]] = []

        def callback(child_hwnd, _lparam):
            try:
                info = _sala_reuniao_window_info(user32, int(child_hwnd))
                if info.get("width", 0) >= 240 and info.get("height", 0) >= 180:
                    children.append(info)
            except Exception:
                pass
            return True

        user32.EnumChildWindows(parent_hwnd, enum_proc(callback), 0)
        candidates = [
            child for child in children
            if child.get("visible") and child.get("class_name") == "Chrome_RenderWidgetHostHWND"
        ]
        if candidates:
            candidates.sort(key=lambda child: (child.get("width", 0) * child.get("height", 0)), reverse=True)
            selected = candidates[0]
            return {
                "hwnd": int(selected["hwnd"]),
                "resolved": True,
                "parent": parent_info,
                "selected": selected,
                "children_count": len(children),
            }
        return {
            "hwnd": int(parent_hwnd),
            "resolved": False,
            "parent": parent_info,
            "children_count": len(children),
            "reason": "render_widget_not_found",
        }
    except Exception as exc:
        return {"hwnd": int(parent_hwnd), "resolved": False, "reason": f"resolve_parent_failed:{exc}"}


def _sala_reuniao_dock_parent(parent_hwnd: int, mode: str = "") -> dict[str, Any]:
    mode_text = str(mode or "").strip().lower().replace("-", "_")
    if mode_text not in {"window", "app_window", "browser_window", "top_window"}:
        return _sala_reuniao_resolve_dock_parent(parent_hwnd)
    if platform.system().lower() != "windows" or not parent_hwnd:
        return {"hwnd": int(parent_hwnd or 0), "resolved": False, "mode": mode_text}
    import ctypes

    user32 = ctypes.windll.user32
    try:
        if not user32.IsWindow(parent_hwnd):
            return {"hwnd": int(parent_hwnd), "resolved": False, "mode": mode_text, "reason": "parent_not_window"}
        _sala_reuniao_prepare_dock_parent_window(user32, parent_hwnd)
        parent_info = _sala_reuniao_window_info(user32, parent_hwnd)
        if parent_info.get("class_name") == "Chrome_WidgetWin_1":
            render_parent = _sala_reuniao_resolve_dock_parent(parent_hwnd)
            if int(render_parent.get("hwnd") or 0) and int(render_parent.get("hwnd") or 0) != int(parent_hwnd):
                render_parent["mode"] = mode_text
                render_parent["coerced_to_render_widget"] = True
                return render_parent
        return {
            "hwnd": int(parent_hwnd),
            "resolved": False,
            "mode": mode_text,
            "parent": parent_info,
            "selected": parent_info,
        }
    except Exception as exc:
        return {"hwnd": int(parent_hwnd), "resolved": False, "mode": mode_text, "reason": f"resolve_parent_failed:{exc}"}


def _sala_reuniao_parent_not_ready(parent_window: dict[str, Any]) -> Optional[dict[str, Any]]:
    reason = str(parent_window.get("reason") or "")
    if reason in {"parent_not_window", "resolve_parent_failed"} or reason.startswith("resolve_parent_failed:"):
        return {"reason": reason or "parent_invalid", "message": "A janela do JK Sistema nao esta disponivel para acoplar o Acesso Remoto. Reabra a aba e tente novamente."}
    info = parent_window.get("selected") if isinstance(parent_window.get("selected"), dict) else parent_window.get("parent")
    if not isinstance(info, dict):
        return {"reason": "parent_invalid", "message": "A janela do JK Sistema nao esta pronta para acoplar o Acesso Remoto. Reabra a aba e tente novamente."}
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    x = int(info.get("x") or 0)
    y = int(info.get("y") or 0)
    if width < 240 or height < 180:
        return {"reason": "parent_too_small", "message": "A janela do JK Sistema ainda nao esta pronta para acoplar o Acesso Remoto."}
    if not bool(info.get("visible")):
        return {"reason": "parent_not_visible", "message": "A janela do JK Sistema ainda nao esta visivel para acoplar o Acesso Remoto."}
    if x <= -20000 or y <= -20000:
        return {"reason": "parent_offscreen", "message": "A janela do JK Sistema esta minimizada ou fora da tela. Abra a Sala de Reuniao e clique em Reencaixar."}
    return None


def _sala_reuniao_occluding_render_widget(user32: Any, parent_hwnd: int, hwnd: int, rect_after: Any) -> Optional[dict[str, Any]]:
    if platform.system().lower() != "windows" or not parent_hwnd or not hwnd:
        return None

    GW_HWNDPREV = 3
    current = int(user32.GetWindow(hwnd, GW_HWNDPREV) or 0)
    width_after = max(1, int(rect_after.right - rect_after.left))
    height_after = max(1, int(rect_after.bottom - rect_after.top))
    rect_area = width_after * height_after
    while current:
        try:
            if int(user32.GetParent(current) or 0) == int(parent_hwnd):
                info = _sala_reuniao_window_info(user32, int(current))
                if bool(info.get("visible")) and info.get("class_name") == "Chrome_RenderWidgetHostHWND":
                    left = max(int(rect_after.left), int(info.get("x") or 0))
                    top = max(int(rect_after.top), int(info.get("y") or 0))
                    right = min(int(rect_after.right), int(info.get("x") or 0) + int(info.get("width") or 0))
                    bottom = min(int(rect_after.bottom), int(info.get("y") or 0) + int(info.get("height") or 0))
                    overlap_width = max(0, right - left)
                    overlap_height = max(0, bottom - top)
                    overlap_ratio = (overlap_width * overlap_height) / rect_area
                    if overlap_ratio >= 0.35:
                        info["overlap_ratio"] = round(overlap_ratio, 4)
                        return info
        except Exception:
            pass
        current = int(user32.GetWindow(current, GW_HWNDPREV) or 0)
    return None


def _sala_reuniao_restore_rustdesk_child_views(user32: Any, hwnd: int, width: int, height: int) -> list[dict[str, Any]]:
    if platform.system().lower() != "windows" or not hwnd:
        return []
    import ctypes
    from ctypes import wintypes

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    SW_SHOW = 5
    SW_RESTORE = 9
    SWP_NOACTIVATE = 0x0010
    SWP_NOZORDER = 0x0004
    SWP_SHOWWINDOW = 0x0040
    restored: list[dict[str, Any]] = []

    def callback(child_hwnd, _lparam):
        try:
            info = _sala_reuniao_window_info(user32, int(child_hwnd))
            class_name = str(info.get("class_name") or "").lower()
            child_width = int(info.get("width") or 0)
            child_height = int(info.get("height") or 0)
            rustdesk_view = "flutter" in class_name or (child_width >= 120 and child_height >= 90)
            if rustdesk_view:
                target_width = max(120, int(width))
                target_height = max(90, int(height))
                user32.ShowWindow(child_hwnd, SW_RESTORE)
                user32.ShowWindow(child_hwnd, SW_SHOW)
                user32.SetWindowPos(
                    child_hwnd,
                    0,
                    0,
                    0,
                    target_width,
                    target_height,
                    SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
                user32.MoveWindow(child_hwnd, 0, 0, target_width, target_height, True)
                restored.append({
                    "hwnd": str(int(child_hwnd)),
                    "class_name": info.get("class_name") or "",
                    "was_visible": bool(info.get("visible")),
                    "width": child_width,
                    "height": child_height,
                })
        except Exception:
            pass
        return True

    try:
        user32.EnumChildWindows(hwnd, enum_proc(callback), 0)
    except Exception:
        return restored
    return restored


def _sala_reuniao_rustdesk_show_external(
    payload: Optional[dict[str, Any]] = None,
    preferred_pid: Optional[int] = None,
    window_timeout: float = 3.0,
) -> dict[str, Any]:
    if platform.system().lower() != "windows":
        return {
            "success": True,
            "running": _sala_reuniao_rustdesk_running(),
            "opened": False,
            "docked": False,
            "external": True,
            "message": "Acesso Remoto aberto em janela externa.",
        }
    import ctypes
    from ctypes import wintypes

    payload = payload if isinstance(payload, dict) else {}
    user32 = ctypes.windll.user32
    window = _sala_reuniao_find_rustdesk_window(timeout_seconds=window_timeout, preferred_pid=preferred_pid)
    hwnd = int(window.get("hwnd") or 0)
    if not hwnd:
        return _sala_reuniao_rustdesk_external(
            "Acesso Remoto esta em execucao, mas a janela principal nao foi encontrada.",
            reason="rustdesk_window_missing",
        )

    GWL_STYLE = -16
    GWL_EXSTYLE = -20
    WS_CHILD = 0x40000000
    WS_POPUP = 0x80000000
    WS_OVERLAPPEDWINDOW = 0x00CF0000
    WS_VISIBLE = 0x10000000
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_SYSMENU = 0x00080000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    WS_EX_APPWINDOW = 0x00040000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_TOPMOST = 0x00000008
    SW_RESTORE = 9
    SW_MAXIMIZE = 3
    SW_SHOW = 5
    SWP_NOOWNERZORDER = 0x0200
    SWP_FRAMECHANGED = 0x0020
    SWP_SHOWWINDOW = 0x0040
    HWND_TOP = 0

    get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

    def signed_style(value: int) -> int:
        value = int(value) & 0xFFFFFFFF
        if value >= 0x80000000:
            return value - 0x100000000
        return value

    requested_parent_hwnd = _sala_reuniao_win_int(payload.get("parent_hwnd") or payload.get("parentHwnd"))
    x = 80
    y = 80
    width = 1180
    height = 760
    try:
        if requested_parent_hwnd and user32.IsWindow(requested_parent_hwnd):
            parent_rect = wintypes.RECT()
            if user32.GetWindowRect(requested_parent_hwnd, ctypes.byref(parent_rect)):
                parent_width = int(parent_rect.right - parent_rect.left)
                parent_height = int(parent_rect.bottom - parent_rect.top)
                x = int(parent_rect.left) + 40
                y = int(parent_rect.top) + 70
                width = max(980, min(parent_width - 80, 1320))
                height = max(620, min(parent_height - 120, 840))
    except Exception:
        pass

    try:
        current_style = int(get_window_long(hwnd, GWL_STYLE)) & 0xFFFFFFFF
        external_style = (
            current_style
            & ~WS_CHILD
            & ~WS_POPUP
        ) | WS_OVERLAPPEDWINDOW | WS_VISIBLE | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
        current_ex_style = int(get_window_long(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
        external_ex_style = (current_ex_style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW & ~WS_EX_TOPMOST
        previous_parent = int(user32.GetParent(hwnd) or 0)
        if previous_parent:
            user32.SetParent(hwnd, 0)
        set_window_long(hwnd, GWL_STYLE, signed_style(external_style))
        set_window_long(hwnd, GWL_EXSTYLE, signed_style(external_ex_style))
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.SetWindowPos(
            hwnd,
            HWND_TOP,
            int(x),
            int(y),
            int(width),
            int(height),
            SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOOWNERZORDER,
        )
        user32.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True)
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        time.sleep(0.15)
        rect_after = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect_after))
        maximized_width = max(320, int(rect_after.right - rect_after.left))
        maximized_height = max(240, int(rect_after.bottom - rect_after.top))
        restored_child_views = _sala_reuniao_restore_rustdesk_child_views(user32, hwnd, maximized_width, maximized_height)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

        user32.GetWindowRect(hwnd, ctypes.byref(rect_after))
        return {
            "success": True,
            "running": True,
            "opened": False,
            "docked": False,
            "external": True,
            "hwnd": str(hwnd),
            "previous_parent": str(previous_parent),
            "maximized": True,
            "rect_after": {
                "x": int(rect_after.left),
                "y": int(rect_after.top),
                "width": int(rect_after.right - rect_after.left),
                "height": int(rect_after.bottom - rect_after.top),
            },
            "restored_child_views": restored_child_views,
            "message": "Acesso Remoto aberto em janela externa confiavel.",
        }
    except Exception as exc:
        return _sala_reuniao_rustdesk_external(
            f"Nao foi possivel restaurar o Acesso Remoto em janela externa: {exc}",
            hwnd=str(hwnd),
            window=window,
            reason="external_restore_failed",
        )


def _sala_reuniao_window_belongs_to_parent(user32: Any, hwnd: int, requested_parent_hwnd: int) -> bool:
    if not requested_parent_hwnd:
        return True
    try:
        current = int(hwnd or 0)
        for _depth in range(12):
            if not current:
                break
            if current == int(requested_parent_hwnd):
                return True
            current = int(user32.GetParent(current) or 0)
        root = int(user32.GetAncestor(hwnd, 2) or 0)  # GA_ROOT
        return root == int(requested_parent_hwnd)
    except Exception:
        return False


def _sala_reuniao_find_rustdesk_window(timeout_seconds: float = 6.0, preferred_pid: Optional[int] = None) -> dict[str, Any]:
    if platform.system().lower() != "windows":
        return {}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    deadline = time.time() + max(0.2, timeout_seconds)
    preferred = int(preferred_pid or 0)
    while time.time() < deadline:
        pids = _sala_reuniao_rustdesk_process_ids()
        if preferred:
            pids.add(preferred)
        candidates: list[dict[str, Any]] = []
        ignored_windows: list[dict[str, Any]] = []

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def collect_window(hwnd) -> None:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) not in pids:
                return
            info = _sala_reuniao_window_info(user32, int(hwnd))
            info["pid"] = int(pid.value)
            eval_info = _sala_reuniao_rustdesk_window_eval(info)
            if eval_info.get("valid"):
                candidates.append(info)
            elif eval_info.get("reason") in {"rejected_class", "offscreen", "too_small", "not_rustdesk_ui"}:
                ignored_windows.append(_sala_reuniao_rustdesk_window_summary(info, str(eval_info.get("reason") or "ignored")))

        def child_callback(child_hwnd, _lparam):
            collect_window(child_hwnd)
            return True

        def callback(hwnd, _lparam):
            collect_window(hwnd)
            user32.EnumChildWindows(hwnd, enum_proc(child_callback), 0)
            return True

        user32.EnumWindows(enum_proc(callback), 0)
        if candidates:
            candidates.sort(
                key=lambda item: (
                    item.get("pid") == preferred,
                    item.get("visible") is True,
                    item.get("enabled") is True,
                    item.get("width", 0) >= 320 and item.get("height", 0) >= 240,
                    3 if str(item.get("class_name") or "").strip().lower() == "rustdeskmultiwindow"
                    else 2 if str(item.get("class_name") or "").strip().lower() == "flutter_runner_win32_window"
                    else 1 if str(item.get("class_name") or "").strip().lower() == "flutterview"
                    else 0,
                    item.get("is_top_level") is True,
                    not item.get("owner"),
                    item.get("area") or 0,
                ),
                reverse=True,
            )
            selected = candidates[0]
            selected["ignored_windows"] = ignored_windows[:12]
            selected["selected_window"] = _sala_reuniao_rustdesk_window_summary(selected, "selected")
            return selected
        time.sleep(0.18)
    return {"ignored_windows": ignored_windows[:12]} if "ignored_windows" in locals() and ignored_windows else {}


def _sala_reuniao_rustdesk_external(message: str, **extra: Any) -> dict[str, Any]:
    return {
        "docked": False,
        "external": True,
        "message": message,
        **extra,
    }


def _sala_reuniao_rustdesk_hide(payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if platform.system().lower() != "windows":
        return {
            "success": True,
            "running": _sala_reuniao_rustdesk_running(),
            "hidden": 0,
            "message": "Ocultacao interna disponivel apenas no Windows.",
        }
    import ctypes
    from ctypes import wintypes

    payload = payload if isinstance(payload, dict) else {}
    if not payload.get("allow_hide"):
        return {
            "success": True,
            "running": _sala_reuniao_rustdesk_running(),
            "hidden": 0,
            "ignored": True,
            "message": "Ocultacao ignorada para evitar corrida entre abas de Acesso Remoto.",
        }
    requested_parent_hwnd = _sala_reuniao_win_int(payload.get("parent_hwnd") or payload.get("parentHwnd"))
    pids = _sala_reuniao_rustdesk_process_ids()
    if not pids:
        return {
            "success": True,
            "running": False,
            "hidden": 0,
            "message": "Acesso Remoto nao esta em execucao.",
        }

    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    SW_HIDE = 0
    SWP_NOACTIVATE = 0x0010
    SWP_NOZORDER = 0x0004
    SWP_HIDEWINDOW = 0x0080
    targets: dict[int, dict[str, Any]] = {}
    parent_rect = None
    if requested_parent_hwnd:
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            if user32.GetWindowRect(requested_parent_hwnd, ctypes.byref(rect)):
                parent_rect = {
                    "left": int(rect.left),
                    "top": int(rect.top),
                    "right": int(rect.right),
                    "bottom": int(rect.bottom),
                }
        except Exception:
            parent_rect = None

    def overlaps_parent(info: dict[str, Any]) -> bool:
        if not parent_rect:
            return False
        try:
            left = int(info.get("x") or 0)
            top = int(info.get("y") or 0)
            right = left + int(info.get("width") or 0)
            bottom = top + int(info.get("height") or 0)
            overlap_width = max(0, min(right, parent_rect["right"]) - max(left, parent_rect["left"]))
            overlap_height = max(0, min(bottom, parent_rect["bottom"]) - max(top, parent_rect["top"]))
            area = max(1, int(info.get("width") or 0) * int(info.get("height") or 0))
            return (overlap_width * overlap_height) / area >= 0.55
        except Exception:
            return False

    def collect_window(hwnd) -> None:
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) not in pids:
                return
            info = _sala_reuniao_window_info(user32, int(hwnd))
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            parent = int(info.get("parent") or 0)
            class_name = str(info.get("class_name") or "").lower()
            title = str(info.get("title") or "").lower()
            visual = width >= 180 and height >= 120
            rustdesk_like = "rustdesk" in title or "flutter" in class_name
            parent_match = bool(requested_parent_hwnd and parent == requested_parent_hwnd)
            if requested_parent_hwnd and not _sala_reuniao_window_belongs_to_parent(user32, int(hwnd), requested_parent_hwnd):
                if not (rustdesk_like and visual and overlaps_parent(info)):
                    return
            if visual or rustdesk_like or parent_match:
                targets[int(hwnd)] = info
        except Exception:
            return

    def child_callback(child_hwnd, _lparam):
        collect_window(child_hwnd)
        return True

    def callback(hwnd, _lparam):
        collect_window(hwnd)
        try:
            user32.EnumChildWindows(hwnd, enum_proc(child_callback), 0)
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc(callback), 0)

    hidden = 0
    hidden_windows: list[dict[str, Any]] = []
    for hwnd, info in targets.items():
        try:
            width = max(320, int(info.get("width") or 900))
            height = max(240, int(info.get("height") or 560))
            user32.ShowWindow(hwnd, SW_HIDE)
            user32.SetWindowPos(
                hwnd,
                0,
                -32000,
                -32000,
                width,
                height,
                SWP_NOZORDER | SWP_NOACTIVATE | SWP_HIDEWINDOW,
            )
            hidden += 1
            hidden_windows.append({
                "hwnd": str(hwnd),
                "parent": str(info.get("parent") or 0),
                "class_name": info.get("class_name") or "",
                "title": info.get("title") or "",
                "width": int(info.get("width") or 0),
                "height": int(info.get("height") or 0),
            })
        except Exception:
            continue

    return {
        "success": True,
        "running": True,
        "opened": False,
        "docked": False,
        "external": False,
        "hidden": hidden,
        "windows": hidden_windows,
        "message": "Acesso Remoto ocultado da aba inativa." if hidden else "Nenhuma janela visivel do Acesso Remoto precisou ser ocultada.",
    }


def _sala_reuniao_rustdesk_should_restore(result: dict[str, Any]) -> bool:
    return str(result.get("reason") or "") in {
        "rustdesk_window_missing",
        "not_visible_after",
        "invalid_rect_after",
    }


def _sala_reuniao_rustdesk_dock_or_restore(
    payload: Optional[dict[str, Any]],
    command: list[str],
    cwd: Optional[str],
    preferred_pid: Optional[int] = None,
    first_timeout: float = 0.8,
) -> dict[str, Any]:
    stale_reset: Optional[dict[str, Any]] = None
    if isinstance(payload, dict) and payload.get("reset_stale_children") and command and platform.system().lower() == "windows":
        parent_hwnd = _sala_reuniao_win_int(payload.get("parent_hwnd") or payload.get("parentHwnd"))
        stale_pids = _sala_reuniao_rustdesk_stale_child_pids(parent_hwnd)
        if stale_pids:
            stale_reset = {
                "stale_child_pids": sorted(stale_pids),
                "process_reset": _sala_reuniao_rustdesk_terminate_pids(stale_pids),
            }
            try:
                fresh_proc = _sala_reuniao_rustdesk_start_process(command, cwd)
                preferred_pid = getattr(fresh_proc, "pid", None)
                stale_reset["fresh_pid"] = preferred_pid
            except Exception as exc:
                stale_reset["fresh_start_error"] = str(exc)

    result = _sala_reuniao_rustdesk_dock(payload, preferred_pid=preferred_pid, window_timeout=first_timeout)
    if stale_reset:
        result["stale_child_reset"] = stale_reset
    if result.get("docked") or not command or platform.system().lower() != "windows":
        return result
    if not _sala_reuniao_rustdesk_should_restore(result):
        return result

    try:
        proc = _sala_reuniao_rustdesk_start_process(command, cwd)
    except Exception as exc:
        return {
            **result,
            "restore_attempted": True,
            "restore_error": str(exc),
            "message": f"{result.get('message') or 'Nao foi possivel acoplar o Acesso Remoto'} Tente abrir o Acesso Remoto manualmente e clique em Reencaixar.",
        }

    restored = _sala_reuniao_rustdesk_dock(payload, preferred_pid=getattr(proc, "pid", None), window_timeout=8.0)
    restored.update({
        "restore_attempted": True,
        "restore_pid": getattr(proc, "pid", None),
        "first_dock_reason": result.get("reason"),
        "first_dock_message": result.get("message"),
    })
    if stale_reset:
        restored["stale_child_reset"] = stale_reset
    reset_reasons = {"not_visible_after", "invalid_rect_after", "rustdesk_window_missing"}
    if (
        not restored.get("docked")
        and command
        and isinstance(payload, dict)
        and (payload.get("contained_mode") or payload.get("force_window_child"))
        and str(restored.get("reason") or "") in reset_reasons
    ):
        target_pids: set[int] = set()
        try:
            if isinstance(restored.get("window"), dict) and restored["window"].get("pid"):
                target_pids.add(int(restored["window"]["pid"]))
        except Exception:
            pass
        try:
            if restored.get("restore_pid"):
                target_pids.add(int(restored["restore_pid"]))
        except Exception:
            pass
        target_pids.update(_sala_reuniao_rustdesk_window_process_ids())
        if not target_pids:
            target_pids.update(_sala_reuniao_rustdesk_process_ids())
        reset_result = _sala_reuniao_rustdesk_terminate_pids(target_pids)
        try:
            fresh_proc = _sala_reuniao_rustdesk_start_process(command, cwd)
            fresh = _sala_reuniao_rustdesk_dock(
                payload,
                preferred_pid=getattr(fresh_proc, "pid", None),
                window_timeout=8.0,
            )
            fresh.update({
                "process_reset_attempted": True,
                "process_reset_reason": restored.get("reason"),
                "process_reset": reset_result,
                "fresh_pid": getattr(fresh_proc, "pid", None),
                "previous_restore_result": {
                    "reason": restored.get("reason"),
                    "message": restored.get("message"),
                    "restore_pid": restored.get("restore_pid"),
                },
            })
            if stale_reset:
                fresh["stale_child_reset"] = stale_reset
            return fresh
        except Exception as exc:
            restored.update({
                "process_reset_attempted": True,
                "process_reset_reason": restored.get("reason"),
                "process_reset": reset_result,
                "process_reset_error": str(exc),
            })
    return restored


def _sala_reuniao_rustdesk_dock(
    payload: Optional[dict[str, Any]],
    preferred_pid: Optional[int] = None,
    window_timeout: float = 6.0,
) -> dict[str, Any]:
    if platform.system().lower() != "windows":
        return {"docked": False, "message": "Acoplamento interno disponivel apenas no Windows."}
    import ctypes
    from ctypes import wintypes

    payload = payload if isinstance(payload, dict) else {}
    requested_parent_hwnd = _sala_reuniao_win_int(payload.get("parent_hwnd") or payload.get("parentHwnd"))
    parent_mode = str(payload.get("parent_mode") or payload.get("parentMode") or payload.get("parentTarget") or "")
    bounds = _sala_reuniao_rustdesk_bounds(payload)
    user32 = ctypes.windll.user32
    if not requested_parent_hwnd:
        return _sala_reuniao_rustdesk_external(
            "Nao foi possivel acoplar o Acesso Remoto: janela do JK Sistema indisponivel. Reinicie o JK Sistema e tente novamente.",
            reason="parent_missing",
        )
    parent_window = _sala_reuniao_dock_parent(requested_parent_hwnd, parent_mode)
    parent_hwnd = int(parent_window.get("hwnd") or requested_parent_hwnd)
    parent_not_ready = _sala_reuniao_parent_not_ready(parent_window)
    if parent_not_ready:
        return _sala_reuniao_rustdesk_external(
            parent_not_ready["message"],
            reason=parent_not_ready["reason"],
            parent_hwnd=str(parent_hwnd),
            requested_parent_hwnd=str(requested_parent_hwnd),
            parent_window=parent_window,
        )

    window = _sala_reuniao_find_rustdesk_window(timeout_seconds=window_timeout, preferred_pid=preferred_pid)
    hwnd = int(window.get("hwnd") or 0)
    if not hwnd:
        return _sala_reuniao_rustdesk_external(
            "Nao foi possivel acoplar o Acesso Remoto: janela do Acesso Remoto nao encontrada.",
            reason="rustdesk_window_missing",
        )

    GWL_STYLE = -16
    WS_POPUP = 0x80000000
    WS_CHILD = 0x40000000
    WS_VISIBLE = 0x10000000
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_SYSMENU = 0x00080000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    WS_CLIPSIBLINGS = 0x04000000
    WS_CLIPCHILDREN = 0x02000000
    GWL_EXSTYLE = -20
    WS_EX_APPWINDOW = 0x00040000
    WS_EX_WINDOWEDGE = 0x00000100
    WS_EX_CLIENTEDGE = 0x00000200
    WS_EX_DLGMODALFRAME = 0x00000001
    WS_EX_TOPMOST = 0x00000008
    SWP_NOACTIVATE = 0x0010
    SWP_NOZORDER = 0x0004
    SWP_FRAMECHANGED = 0x0020
    SWP_SHOWWINDOW = 0x0040
    SW_SHOW = 5
    SW_RESTORE = 9
    HWND_TOP = 0

    get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

    def signed_style(value: int) -> int:
        value = int(value) & 0xFFFFFFFF
        if value >= 0x80000000:
            return value - 0x100000000
        return value

    try:
        parent_rect_before = wintypes.RECT()
        user32.GetWindowRect(parent_hwnd, ctypes.byref(parent_rect_before))

        if not bool(window.get("visible")):
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.ShowWindow(hwnd, SW_SHOW)
            for _attempt in range(8):
                time.sleep(0.25)
                refreshed_window = _sala_reuniao_find_rustdesk_window(timeout_seconds=0.25, preferred_pid=preferred_pid)
                refreshed_hwnd = int(refreshed_window.get("hwnd") or 0)
                if refreshed_hwnd:
                    window = refreshed_window
                    hwnd = refreshed_hwnd
                if bool(window.get("visible")):
                    break

        try:
            user32.SetLastError(0)
            user32.SetForegroundWindow(parent_hwnd)
        except Exception:
            pass
        current_style = int(get_window_long(hwnd, GWL_STYLE)) & 0xFFFFFFFF
        child_style = (
            current_style
            & ~WS_POPUP
            & ~WS_CAPTION
            & ~WS_THICKFRAME
            & ~WS_SYSMENU
            & ~WS_MINIMIZEBOX
            & ~WS_MAXIMIZEBOX
        ) | WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN
        current_ex_style = int(get_window_long(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
        child_ex_style = (
            current_ex_style
            & ~WS_EX_APPWINDOW
            & ~WS_EX_WINDOWEDGE
            & ~WS_EX_CLIENTEDGE
            & ~WS_EX_DLGMODALFRAME
            & ~WS_EX_TOPMOST
        )
        previous_parent = int(user32.SetParent(hwnd, parent_hwnd) or 0)
        set_window_long(hwnd, GWL_STYLE, signed_style(child_style))
        set_window_long(hwnd, GWL_EXSTYLE, signed_style(child_ex_style))

        for attempt in range(4):
            if attempt:
                time.sleep(0.25)
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.ShowWindow(hwnd, SW_SHOW)
            user32.SetWindowPos(
                hwnd,
                HWND_TOP,
                int(bounds["x"]),
                int(bounds["y"]),
                int(bounds["width"]),
                int(bounds["height"]),
                SWP_FRAMECHANGED | SWP_SHOWWINDOW,
            )
            user32.MoveWindow(hwnd, int(bounds["x"]), int(bounds["y"]), int(bounds["width"]), int(bounds["height"]), True)
            try:
                user32.BringWindowToTop(hwnd)
            except Exception:
                pass

        restored_child_views = _sala_reuniao_restore_rustdesk_child_views(
            user32,
            hwnd,
            int(bounds["width"]),
            int(bounds["height"]),
        )

        parent_rect_current = wintypes.RECT()
        user32.GetWindowRect(parent_hwnd, ctypes.byref(parent_rect_current))
        parent_moved = (
            abs(int(parent_rect_current.left - parent_rect_before.left)) > 8
            or abs(int(parent_rect_current.top - parent_rect_before.top)) > 8
            or abs(int((parent_rect_current.right - parent_rect_current.left) - (parent_rect_before.right - parent_rect_before.left))) > 8
            or abs(int((parent_rect_current.bottom - parent_rect_current.top) - (parent_rect_before.bottom - parent_rect_before.top))) > 8
        )
        if parent_moved:
            user32.SetWindowPos(
                parent_hwnd,
                HWND_TOP,
                int(parent_rect_before.left),
                int(parent_rect_before.top),
                int(parent_rect_before.right - parent_rect_before.left),
                int(parent_rect_before.bottom - parent_rect_before.top),
                SWP_NOZORDER | SWP_NOACTIVATE,
            )
            time.sleep(0.15)
            user32.SetWindowPos(
                hwnd,
                HWND_TOP,
                int(bounds["x"]),
                int(bounds["y"]),
                int(bounds["width"]),
                int(bounds["height"]),
                SWP_FRAMECHANGED | SWP_SHOWWINDOW,
            )
            user32.MoveWindow(hwnd, int(bounds["x"]), int(bounds["y"]), int(bounds["width"]), int(bounds["height"]), True)
            restored_child_views.extend(_sala_reuniao_restore_rustdesk_child_views(
                user32,
                hwnd,
                int(bounds["width"]),
                int(bounds["height"]),
            ))

        actual_parent = int(user32.GetParent(hwnd) or 0)
        if actual_parent != int(parent_hwnd):
            return _sala_reuniao_rustdesk_external(
                "Nao foi possivel acoplar o Acesso Remoto: o Windows nao aceitou prender a janela ao JK Sistema.",
                hwnd=str(hwnd),
                parent_hwnd=str(parent_hwnd),
                requested_parent_hwnd=str(requested_parent_hwnd),
                actual_parent=str(actual_parent),
                previous_parent=str(previous_parent),
                window=window,
                parent_window=parent_window,
                reason="set_parent_failed",
            )
        rect_after = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect_after))
        width_after = int(rect_after.right - rect_after.left)
        height_after = int(rect_after.bottom - rect_after.top)
        visible_after = bool(user32.IsWindowVisible(hwnd))
        parent_rect_after = wintypes.RECT()
        user32.GetWindowRect(parent_hwnd, ctypes.byref(parent_rect_after))
        overlap_width = max(0, min(int(rect_after.right), int(parent_rect_after.right)) - max(int(rect_after.left), int(parent_rect_after.left)))
        overlap_height = max(0, min(int(rect_after.bottom), int(parent_rect_after.bottom)) - max(int(rect_after.top), int(parent_rect_after.top)))
        overlap_area = overlap_width * overlap_height
        rect_area = max(1, width_after * height_after)
        overlap_ratio = overlap_area / rect_area
        if not visible_after:
            return _sala_reuniao_rustdesk_external(
                "Nao foi possivel acoplar o Acesso Remoto: a janela acoplada ficou oculta.",
                hwnd=str(hwnd),
                parent_hwnd=str(parent_hwnd),
                requested_parent_hwnd=str(requested_parent_hwnd),
                rect_after={"width": width_after, "height": height_after},
                visible_after=visible_after,
                window=window,
                parent_window=parent_window,
                reason="not_visible_after",
            )
        if overlap_ratio < 0.65:
            return _sala_reuniao_rustdesk_external(
                "Nao foi possivel acoplar o Acesso Remoto: a janela ficou fora do painel da Sala de Reuniao.",
                hwnd=str(hwnd),
                parent_hwnd=str(parent_hwnd),
                requested_parent_hwnd=str(requested_parent_hwnd),
                rect_after={
                    "x": int(rect_after.left),
                    "y": int(rect_after.top),
                    "width": width_after,
                    "height": height_after,
                    "overlap_ratio": round(overlap_ratio, 4),
                },
                visible_after=visible_after,
                window=window,
                parent_window=parent_window,
                reason="outside_parent_after",
            )
        if width_after < 120 or height_after < 90:
            return _sala_reuniao_rustdesk_external(
                "Nao foi possivel acoplar o Acesso Remoto: a janela acoplada ficou sem tamanho visivel.",
                hwnd=str(hwnd),
                parent_hwnd=str(parent_hwnd),
                requested_parent_hwnd=str(requested_parent_hwnd),
                rect_after={"width": width_after, "height": height_after},
                visible_after=visible_after,
                window=window,
                parent_window=parent_window,
                reason="invalid_rect_after",
            )
        contained_mode = bool(payload.get("contained_mode") or payload.get("force_window_child"))
        occluding_render = None if contained_mode else _sala_reuniao_occluding_render_widget(user32, parent_hwnd, hwnd, rect_after)
        if occluding_render:
            mode_text = parent_mode.strip().lower().replace("-", "_")
            if not payload.get("occlusion_retry") and mode_text in {"window", "app_window", "browser_window", "top_window"}:
                retry_payload = dict(payload)
                retry_payload["parent_mode"] = "render_widget"
                retry_payload["occlusion_retry"] = True
                retry_result = _sala_reuniao_rustdesk_dock(
                    retry_payload,
                    preferred_pid=preferred_pid,
                    window_timeout=0.5,
                )
                retry_result["occlusion_retry"] = True
                retry_result["first_parent_hwnd"] = str(parent_hwnd)
                retry_result["first_occluding_render_widget"] = occluding_render
                return retry_result
            return _sala_reuniao_rustdesk_external(
                "Nao foi possivel acoplar o Acesso Remoto: a janela ficou atras do conteudo do Electron.",
                hwnd=str(hwnd),
                parent_hwnd=str(parent_hwnd),
                requested_parent_hwnd=str(requested_parent_hwnd),
                rect_after={
                    "x": int(rect_after.left),
                    "y": int(rect_after.top),
                    "width": width_after,
                    "height": height_after,
                    "overlap_ratio": round(overlap_ratio, 4),
                },
                visible_after=visible_after,
                window=window,
                parent_window=parent_window,
                occluding_render_widget=occluding_render,
                reason="occluded_by_render_widget",
            )
    except Exception as exc:
        return _sala_reuniao_rustdesk_external(
            f"Nao foi possivel acoplar o Acesso Remoto: {exc}",
            hwnd=str(hwnd),
            parent_hwnd=str(parent_hwnd),
            requested_parent_hwnd=str(requested_parent_hwnd),
            window=window,
            parent_window=parent_window,
            reason="exception",
        )

    return {
        "docked": True,
        "external": False,
        "hwnd": str(hwnd),
        "parent_hwnd": str(parent_hwnd),
        "requested_parent_hwnd": str(requested_parent_hwnd),
        "bounds": bounds,
        "window": window,
        "selected_window": window.get("selected_window") if isinstance(window, dict) else None,
        "ignored_windows": window.get("ignored_windows", []) if isinstance(window, dict) else [],
        "parent_window": parent_window,
        "visible_after": visible_after,
        "parent_moved_corrected": parent_moved,
        "restored_child_views": restored_child_views,
        "contained_mode": bool(payload.get("contained_mode") or payload.get("force_window_child")),
        "rect_after": {
            "x": int(rect_after.left),
            "y": int(rect_after.top),
            "width": width_after,
            "height": height_after,
            "overlap_ratio": round(overlap_ratio, 4),
        },
        "message": "Acesso Remoto acoplado ao JK Sistema.",
    }


async def sala_reuniao_rustdesk_status(_client_id: str = Depends(sala_context.get_tenant_id)):
    executable = _sala_reuniao_rustdesk_executable()
    return {
        "success": True,
        "available": bool(executable) or platform.system().lower() == "darwin",
        "running": _sala_reuniao_rustdesk_running(),
        "executable": executable,
    }


async def sala_reuniao_rustdesk_abrir(payload: Optional[dict[str, Any]] = Body(default=None), _client_id: str = Depends(sala_context.get_tenant_id)):
    wants_dock = isinstance(payload, dict) and payload.get("dock")
    parent_hwnd = _sala_reuniao_win_int(payload.get("parent_hwnd") or payload.get("parentHwnd")) if isinstance(payload, dict) else 0
    executable = _sala_reuniao_rustdesk_executable()
    was_running = _sala_reuniao_rustdesk_running()
    command, cwd = _sala_reuniao_rustdesk_launch_command(executable)
    if wants_dock and platform.system().lower() == "windows" and not parent_hwnd and not was_running:
        return {
            "success": True,
            "opened": False,
            "running": False,
            "already_running": False,
            "docked": False,
            "external": False,
            "message": "Acesso Remoto nao foi aberto fora do JK Sistema. Reinicie o JK Sistema para ativar o acoplamento interno.",
            "reason": "parent_missing",
        }
    if wants_dock and platform.system().lower() == "windows" and parent_hwnd:
        parent_mode = str(payload.get("parent_mode") or payload.get("parentMode") or payload.get("parentTarget") or "") if isinstance(payload, dict) else ""
        parent_window = _sala_reuniao_dock_parent(parent_hwnd, parent_mode)
        parent_not_ready = _sala_reuniao_parent_not_ready(parent_window)
        if parent_not_ready:
            return {
                "success": True,
                "opened": False,
                "running": was_running,
                "already_running": was_running,
                "docked": False,
                "external": False,
                "message": parent_not_ready["message"],
                "reason": parent_not_ready["reason"],
                "parent_window": parent_window,
            }
    if not command:
        if was_running:
            response = {
                "success": True,
                "opened": False,
                "running": True,
                "already_running": True,
                "docked": False,
                "external": True,
                "executable": "",
                "message": "Acesso Remoto ja esta em execucao.",
            }
            if isinstance(payload, dict) and payload.get("dock"):
                response.update(_sala_reuniao_rustdesk_dock(payload, window_timeout=1.0))
            else:
                response.update(_sala_reuniao_rustdesk_show_external(payload, window_timeout=1.0))
            return response
        raise HTTPException(
            status_code=404,
            detail="Acesso Remoto nao encontrado. Instale o Acesso Remoto ou configure JK_RUSTDESK_PATH com o caminho do executavel.",
        )
    if was_running:
        response = {
            "success": True,
            "opened": False,
            "running": True,
            "already_running": True,
            "docked": False,
            "external": True,
            "executable": executable,
            "message": "Acesso Remoto ja esta em execucao.",
        }
        if isinstance(payload, dict) and payload.get("dock"):
            dock_result = _sala_reuniao_rustdesk_dock_or_restore(payload, command, cwd, first_timeout=1.0)
            response.update(dock_result)
            response["restored"] = bool(dock_result.get("restore_attempted"))
        else:
            external_result = _sala_reuniao_rustdesk_show_external(payload, window_timeout=1.0)
            if str(external_result.get("reason") or "") == "rustdesk_window_missing" and command:
                try:
                    proc = _sala_reuniao_rustdesk_start_process(command, cwd)
                    external_result = _sala_reuniao_rustdesk_show_external(
                        payload,
                        preferred_pid=getattr(proc, "pid", None),
                        window_timeout=8.0,
                    )
                    external_result["restore_attempted"] = True
                    external_result["restore_pid"] = getattr(proc, "pid", None)
                except Exception as exc:
                    external_result["restore_attempted"] = True
                    external_result["restore_error"] = str(exc)
            response.update(external_result)
        return response
    try:
        proc = _sala_reuniao_rustdesk_start_process(command, cwd)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Nao foi possivel abrir o Acesso Remoto: {exc}") from exc
    response = {
        "success": True,
        "opened": True,
        "running": True,
        "already_running": was_running,
        "docked": False,
        "external": True,
        "pid": getattr(proc, "pid", None),
        "executable": executable,
        "message": "Acesso Remoto aberto.",
    }
    if isinstance(payload, dict) and payload.get("dock"):
        dock_result = _sala_reuniao_rustdesk_dock_or_restore(
            payload,
            command,
            cwd,
            preferred_pid=getattr(proc, "pid", None),
            first_timeout=8.0,
        )
        response.update(dock_result)
        response["restored"] = bool(dock_result.get("restore_attempted"))
    else:
        response.update(_sala_reuniao_rustdesk_show_external(
            payload,
            preferred_pid=getattr(proc, "pid", None),
            window_timeout=8.0,
        ))
    return response


async def sala_reuniao_rustdesk_acoplar(payload: Optional[dict[str, Any]] = Body(default=None), _client_id: str = Depends(sala_context.get_tenant_id)):
    executable = _sala_reuniao_rustdesk_executable()
    command, cwd = _sala_reuniao_rustdesk_launch_command(executable)
    result = _sala_reuniao_rustdesk_dock_or_restore(payload, command, cwd, first_timeout=1.0)
    return {
        "success": True,
        **result,
    }


async def sala_reuniao_rustdesk_ocultar(payload: Optional[dict[str, Any]] = Body(default=None)):
    return _sala_reuniao_rustdesk_hide(payload)


async def sala_reuniao_status(_client_id: str = Depends(sala_context.get_tenant_id)):
    """Informa se a chave Daily esta disponivel para criacao automatica de salas."""
    domain = str(os.getenv("DAILY_DOMAIN") or os.getenv("JK_DAILY_DOMAIN") or "").strip()
    return {
        "success": True,
        "daily_configurado": bool(_sala_reuniao_daily_api_key()),
        "daily_domain": domain,
        "recursos": {
            "salas": True,
            "token_host": True,
            "compartilhar_tela": True,
            "chat": True,
            "gravacao": False,
            "transcricao": False,
            "live_streaming": False,
            "legendas": False,
            "sala_espera": True,
            "salas_grupo": True,
            "dialout": False,
            "limite_manual_participantes": False,
            "chamadas_grandes": False,
            "cancelamento_ruido": False,
        },
        "recursos_pagos_removidos": [
            "gravacao_cloud",
            "transcricao",
            "live_rtmp",
            "dialout",
            "limite_manual_participantes",
            "chamadas_grandes",
            "cancelamento_ruido",
        ],
    }

async def sala_reuniao_criar_sala(req: SalaReuniaoCriarSalaRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Cria uma sala Daily Prebuilt quando DAILY_API_KEY ou JK_DAILY_API_KEY esta configurada."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    api_key = _sala_reuniao_daily_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Configure DAILY_API_KEY ou JK_DAILY_API_KEY para criar salas automaticamente.")
    uso = _sala_reuniao_resumo_uso(_client_id)
    if uso.get("blocked"):
        usado = float(uso.get("participant_minutes") or 0)
        bloqueio = int(uso.get("block_participant_minutes") or SALA_REUNIAO_BLOCK_PARTICIPANT_MINUTES)
        reset = uso.get("resets_at") or "dia 1"
        raise HTTPException(
            status_code=429,
            detail=(
                f"Uso mensal da Sala de Reuniao atingiu {usado:.1f} de {bloqueio} participant-minutes. "
                f"O modulo fica bloqueado ate o proximo reset em {reset}."
            ),
        )

    privacidade = _sala_reuniao_privacidade(req.privacidade)
    idioma = _sala_reuniao_idioma(req.idioma)
    try:
        expira_em = int(req.expira_em_minutos) if req.expira_em_minutos is not None else 0
    except Exception:
        expira_em = 0
    exp_timestamp = 0
    if expira_em > 0:
        expira_em = max(15, min(expira_em, 480))
        exp_timestamp = int(time.time()) + (expira_em * 60)
    nome_sala = _sala_reuniao_daily_room_name(req.nome)
    properties: dict[str, Any] = {
        "enable_prejoin_ui": _sala_reuniao_bool(req.habilitar_prejoin, True),
        "enable_knocking": _sala_reuniao_bool(req.habilitar_sala_espera, False),
        "enable_screenshare": _sala_reuniao_bool(req.habilitar_compartilhar_tela, True),
        "enable_chat": _sala_reuniao_bool(req.habilitar_chat, True),
        "enable_shared_chat_history": _sala_reuniao_bool(req.habilitar_historico_chat, True),
        "enable_advanced_chat": _sala_reuniao_bool(req.habilitar_chat_avancado, True),
        "enable_people_ui": _sala_reuniao_bool(req.habilitar_pessoas, True),
        "enable_hand_raising": _sala_reuniao_bool(req.habilitar_mao_levantada, True),
        "enable_emoji_reactions": _sala_reuniao_bool(req.habilitar_reacoes, True),
        "enable_pip_ui": _sala_reuniao_bool(req.habilitar_pip, True),
        "enable_network_ui": _sala_reuniao_bool(req.habilitar_rede, True),
        "enable_live_captions_ui": False,
        "enable_noise_cancellation_ui": False,
        "enable_video_processing_ui": _sala_reuniao_bool(req.habilitar_fundo_virtual, True),
        "enable_breakout_rooms": _sala_reuniao_bool(req.habilitar_salas_grupo, False),
        "enable_cpu_warning_notifications": _sala_reuniao_bool(req.habilitar_alerta_cpu, True),
        "enable_hidden_participants": False,
        "experimental_optimize_large_calls": False,
        "enable_adaptive_simulcast": False,
        "enable_multiparty_adaptive_simulcast": False,
        "enforce_unique_user_ids": _sala_reuniao_bool(req.exigir_user_id_unico, False),
        "enable_terse_logging": False,
        "enable_dialout": False,
        "eject_at_room_exp": _sala_reuniao_bool(req.ejetar_na_expiracao, False),
        "lang": idioma,
        "start_audio_off": _sala_reuniao_bool(req.iniciar_audio_desligado, True),
        "start_video_off": _sala_reuniao_bool(req.iniciar_video_desligado, True),
    }
    if exp_timestamp > 0:
        properties["exp"] = exp_timestamp
    if req.duracao_maxima_minutos:
        duracao = max(5, min(int(req.duracao_maxima_minutos), 480))
        properties["eject_after_elapsed"] = duracao * 60

    payload = {
        "name": nome_sala,
        "privacy": privacidade,
        "properties": properties,
    }

    data = _sala_reuniao_daily_post("/rooms", api_key, payload)
    host_token = None
    token_data: dict[str, Any] = {}
    if _sala_reuniao_bool(req.criar_token_host, True):
        token_data = _sala_reuniao_criar_token_host(api_key, data.get("name") or nome_sala, req, exp_timestamp, idioma)
        host_token = token_data.get("token")
    room_url = data.get("url")
    _sala_reuniao_registrar_sala(_client_id, {
        "name": data.get("name") or nome_sala,
        "url": room_url,
        "privacy": data.get("privacy") or privacidade,
    }, host_token, exp_timestamp, sessao.get("username") or "")
    return {
        "success": True,
        "room": {
            "name": data.get("name") or nome_sala,
            "url": room_url,
            "host_url": _sala_reuniao_url_com_token(room_url, host_token),
            "privacy": data.get("privacy") or privacidade,
            "expires_at": datetime.utcfromtimestamp(exp_timestamp).isoformat() + "Z" if exp_timestamp > 0 else "",
            "config": data.get("config") or data.get("properties") or {},
            "requested_config": properties,
        },
        "host_token": host_token,
        "token": token_data,
    }

async def sala_reuniao_reunioes_ativas(_client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Lista salas abertas e sessoes em andamento para a tela inicial do modulo."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    data = _sala_reuniao_listar_reunioes_ativas(_client_id, sessao)
    return {
        "success": True,
        "daily_configurado": bool(_sala_reuniao_daily_api_key()),
        "is_admin": bool(sessao.get("is_admin")),
        "rooms": data.get("rooms") or [],
        "warning": data.get("warning") or "",
    }

async def sala_reuniao_salas_ativas_alias(_client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Alias de compatibilidade para listar reunioes ativas."""
    return await sala_reuniao_reunioes_ativas(_client_id, authorization)

async def sala_reuniao_salas_listar(_client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Alias GET para listar salas ativas sem conflitar com o POST de criacao."""
    return await sala_reuniao_reunioes_ativas(_client_id, authorization)

async def sala_reuniao_encerrar_local(req: SalaReuniaoEncerrarLocalRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Oculta localmente uma sala que acabou de ser encerrada no navegador."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    _sala_reuniao_exigir_permissao_encerrar(_client_id, req, sessao)
    data = _sala_reuniao_marcar_sala_encerrada(_client_id, req, sessao)
    return {
        "success": True,
        "rooms": data.get("rooms") or [],
        "warning": data.get("warning") or "",
    }

async def sala_reuniao_encerrar(req: SalaReuniaoEncerrarLocalRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Encerra uma sala Daily quando possivel e remove da lista local de reunioes ativas."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    data = _sala_reuniao_encerrar_sala(_client_id, req, sessao)
    return {
        "success": True,
        "rooms": data.get("rooms") or [],
        "warning": data.get("warning") or "",
        "daily_encerrada": bool(data.get("daily_encerrada")),
    }

async def sala_reuniao_encerrar_todas(req: SalaReuniaoEncerrarTodasRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Encerra todas as salas que aparecem na lista de reunioes ativas."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    if not sessao.get("is_admin"):
        raise HTTPException(status_code=403, detail="Apenas administradores podem encerrar todas as reunioes.")
    try:
        suppress_seconds = int(req.suppress_seconds or 1800)
    except Exception:
        suppress_seconds = 1800
    suppress_seconds = max(300, min(suppress_seconds, 7200))
    atuais = _sala_reuniao_listar_reunioes_ativas(_client_id, sessao).get("rooms") or []
    warnings: list[str] = []
    encerradas = 0
    daily_encerradas = 0
    data = {"rooms": atuais, "warning": ""}
    for room in atuais:
        if not isinstance(room, dict):
            continue
        encerradas += 1
        result = _sala_reuniao_encerrar_sala(_client_id, SalaReuniaoEncerrarLocalRequest(
            room_name=room.get("name") or room.get("room_name") or "",
            room_url=room.get("url") or "",
            participant_count=room.get("participants_count") or 0,
            suppress_seconds=suppress_seconds,
        ), sessao)
        data = result
        if result.get("daily_encerrada"):
            daily_encerradas += 1
        if result.get("warning"):
            warnings.append(str(result.get("warning")))
    warning = "; ".join(dict.fromkeys([w for w in warnings if w]))
    return {
        "success": True,
        "rooms": data.get("rooms") or [],
        "warning": warning,
        "encerradas": encerradas,
        "daily_encerradas": daily_encerradas,
    }

async def sala_reuniao_encerrar_local_alias(req: SalaReuniaoEncerrarLocalRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Alias de compatibilidade para marcar uma sala como encerrada localmente."""
    return await sala_reuniao_encerrar_local(req, _client_id, authorization)

async def sala_reuniao_uso_mensal(_client_id: str = Depends(sala_context.get_tenant_id)):
    """Retorna o uso estimado em participant-minutes da sala de reuniao no mes atual."""
    return {
        "success": True,
        "usage": _sala_reuniao_resumo_uso(_client_id),
    }

async def sala_reuniao_uso_mensal_adicionar(req: SalaReuniaoUsoAdicionarRequest, _client_id: str = Depends(sala_context.get_tenant_id)):
    """Acumula participant-seconds estimados pela interface durante uma chamada."""
    return {
        "success": True,
        "usage": _sala_reuniao_adicionar_uso(_client_id, req),
    }

async def sala_reuniao_gravacoes(room_name: str = "", limit: int = 20, _client_id: str = Depends(sala_context.get_tenant_id)):
    """Lista gravacoes cloud armazenadas no Daily para esta conta."""
    api_key = _sala_reuniao_daily_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Configure DAILY_API_KEY ou JK_DAILY_API_KEY para consultar gravacoes.")
    params: dict[str, Any] = {"limit": max(1, min(int(limit or 20), 100))}
    room_name = str(room_name or "").strip()
    if room_name:
        params["room_name"] = room_name
    data = _sala_reuniao_daily_get("/recordings", api_key, params)
    return {
        "success": True,
        "total_count": data.get("total_count", 0),
        "recordings": data.get("data", []),
        "raw": data,
    }

async def sala_reuniao_transcricoes(room_id: str = "", mtg_session_id: str = "", limit: int = 20, _client_id: str = Depends(sala_context.get_tenant_id)):
    """Lista transcricoes geradas pelo Daily."""
    api_key = _sala_reuniao_daily_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Configure DAILY_API_KEY ou JK_DAILY_API_KEY para consultar transcricoes.")
    params: dict[str, Any] = {"limit": max(1, min(int(limit or 20), 100))}
    room_id = str(room_id or "").strip()
    mtg_session_id = str(mtg_session_id or "").strip()
    if room_id:
        params["roomId"] = room_id
    if mtg_session_id:
        params["mtgSessionId"] = mtg_session_id
    data = _sala_reuniao_daily_get("/transcript", api_key, params)
    return {
        "success": True,
        "total_count": data.get("total_count", 0),
        "transcripts": data.get("data", []),
        "raw": data,
    }

SALA_REUNIAO_ENDPOINTS = ('sala_reuniao_status', 'sala_reuniao_criar_sala', 'sala_reuniao_reunioes_ativas', 'sala_reuniao_salas_ativas_alias', 'sala_reuniao_salas_listar', 'sala_reuniao_encerrar_local', 'sala_reuniao_encerrar', 'sala_reuniao_encerrar_todas', 'sala_reuniao_encerrar_local_alias', 'sala_reuniao_uso_mensal', 'sala_reuniao_uso_mensal_adicionar', 'sala_reuniao_gravacoes', 'sala_reuniao_transcricoes', 'sala_reuniao_rustdesk_status', 'sala_reuniao_rustdesk_abrir', 'sala_reuniao_rustdesk_acoplar', 'sala_reuniao_rustdesk_ocultar')

__all__ = ['sala_reuniao_status', 'sala_reuniao_criar_sala', 'sala_reuniao_reunioes_ativas', 'sala_reuniao_salas_ativas_alias', 'sala_reuniao_salas_listar', 'sala_reuniao_encerrar_local', 'sala_reuniao_encerrar', 'sala_reuniao_encerrar_todas', 'sala_reuniao_encerrar_local_alias', 'sala_reuniao_uso_mensal', 'sala_reuniao_uso_mensal_adicionar', 'sala_reuniao_gravacoes', 'sala_reuniao_transcricoes', 'sala_reuniao_rustdesk_status', 'sala_reuniao_rustdesk_abrir', 'sala_reuniao_rustdesk_acoplar', 'sala_reuniao_rustdesk_ocultar', 'SALA_REUNIAO_ENDPOINTS']
