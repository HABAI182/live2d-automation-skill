"""Experimental official Java Access Bridge client, scoped to one Cubism PID."""

import ctypes as C
import time
from ctypes import wintypes as W
from pathlib import Path

import psutil


class Info(C.Structure):
    _fields_ = [
        ("name", C.c_wchar * 1024),
        ("description", C.c_wchar * 1024),
        ("role", C.c_wchar * 256),
        ("role_en_US", C.c_wchar * 256),
        ("states", C.c_wchar * 256),
        ("states_en_US", C.c_wchar * 256),
    ] + [
        (k, C.c_int)
        for k in [
            "indexInParent",
            "childrenCount",
            "x",
            "y",
            "width",
            "height",
            "accessibleComponent",
            "accessibleAction",
            "accessibleSelection",
            "accessibleText",
            "accessibleInterfaces",
        ]
    ]


class Action(C.Structure):
    _fields_ = [("name", C.c_wchar * 256)]


class Actions(C.Structure):
    _fields_ = [("count", C.c_int), ("items", Action * 256)]


class Todo(C.Structure):
    _fields_ = [("count", C.c_int), ("items", Action * 32)]


class Bridge:
    def __init__(self, pid):
        if not hasattr(C, "WinDLL"):
            raise RuntimeError("Java Access Bridge requires Windows")
        self.pid = pid
        self.refs = []
        exe = Path(psutil.Process(pid).exe()).resolve()
        if exe.name.lower() != "java.exe" or not (exe.parents[3] / "CubismEditor5.exe").is_file():
            raise RuntimeError("Target must be a Cubism bundled Java process")
        self.user = C.WinDLL("user32", use_last_error=True)
        self.dll = C.CDLL(str(exe.parent / "windowsaccessbridge-64.dll"))
        specs = {
            "Windows_run": ([], None),
            "isJavaWindow": ([W.HWND], W.BOOL),
            "getAccessibleContextFromHWND": (
                [W.HWND, C.POINTER(C.c_long), C.POINTER(C.c_longlong)],
                W.BOOL,
            ),
            "getAccessibleContextInfo": ([C.c_long, C.c_longlong, C.POINTER(Info)], W.BOOL),
            "getAccessibleChildFromContext": ([C.c_long, C.c_longlong, C.c_int], C.c_longlong),
            "releaseJavaObject": ([C.c_long, C.c_longlong], None),
            "getAccessibleActions": ([C.c_long, C.c_longlong, C.POINTER(Actions)], W.BOOL),
            "doAccessibleActions": (
                [C.c_long, C.c_longlong, C.POINTER(Todo), C.POINTER(C.c_int)],
                W.BOOL,
            ),
            "setTextContents": ([C.c_long, C.c_longlong, C.c_wchar_p], W.BOOL),
            "addAccessibleSelectionFromContext": ([C.c_long, C.c_longlong, C.c_int], None),
            "requestFocus": ([C.c_long, C.c_longlong], W.BOOL),
        }
        for name, (args, result) in specs.items():
            fn = getattr(self.dll, name)
            fn.argtypes = args
            fn.restype = result
        self.user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        self.dll.Windows_run()
        self.pump(0.6)

    def pump(self, seconds=0.15):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            msg = W.MSG()
            while self.user.PeekMessageW(C.byref(msg), None, 0, 0, 1):
                self.user.TranslateMessage(C.byref(msg))
                self.user.DispatchMessageW(C.byref(msg))
            time.sleep(0.01)

    def windows(self):
        windows = []

        @C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        def enum(hwnd, param):
            pid = W.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, C.byref(pid))
            if (
                pid.value == self.pid
                and self.user.IsWindowVisible(hwnd)
                and self.dll.isJavaWindow(hwnd)
            ):
                title = C.create_unicode_buffer(1024)
                self.user.GetWindowTextW(hwnd, title, 1024)
                vm = C.c_long()
                ac = C.c_longlong()
                if self.dll.getAccessibleContextFromHWND(hwnd, C.byref(vm), C.byref(ac)):
                    self.refs.append((vm.value, ac.value))
                    windows.append(
                        {"hwnd": hwnd, "title": title.value, "vm": vm.value, "ac": ac.value}
                    )
            return True

        self.user.EnumWindows(enum, 0)
        return windows

    def info(self, vm, ac):
        info = Info()
        if not self.dll.getAccessibleContextInfo(vm, ac, C.byref(info)):
            raise RuntimeError("Context unavailable")
        return info

    def walk(self, vm, ac, path="", depth=0, max_depth=12, budget=None):
        if budget is None:
            budget = [20000, time.monotonic() + 10]
        budget[0] -= 1
        if budget[0] < 0 or time.monotonic() > budget[1]:
            raise RuntimeError("Accessibility tree exceeded the bounded traversal budget")
        info = self.info(vm, ac)
        yield {
            "path": path,
            "name": info.name,
            "description": info.description,
            "role": info.role_en_US,
            "states": info.states_en_US,
            "children": info.childrenCount,
            "vm": vm,
            "ac": ac,
        }
        if depth >= max_depth:
            return
        for index in range(min(info.childrenCount, 500)):
            child = self.dll.getAccessibleChildFromContext(vm, ac, index)
            if child:
                self.refs.append((vm, child))
                yield from self.walk(
                    vm, child, path + "/" + str(index), depth + 1, max_depth, budget
                )

    def close(self):
        for vm, ac in reversed(self.refs):
            self.dll.releaseJavaObject(vm, ac)
        self.refs = []
