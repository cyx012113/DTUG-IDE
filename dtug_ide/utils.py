"""Utility functions for DTUG IDE."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional


def generate_uid() -> str:
    """Generate a short unique identifier."""
    return uuid.uuid4().hex[:8]


def find_app_icon() -> Optional[str]:
    """Locate the application .ico used for window/file icons.

    Works both in development (``python -m dtug_ide``) and in a frozen
    PyInstaller build. Returns a path string, or ``None`` if not found.
    """
    candidates: List[Path] = []
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidates.append(base / "dtug-ide.ico")
        candidates.append(base / "_internal" / "dtug-ide.ico")
        candidates.append(base / "packaging" / "dtug-ide.ico")
    else:
        base = Path(__file__).resolve().parent.parent  # project root
        candidates.append(base / "packaging" / "dtug-ide.ico")
        candidates.append(base / "docs" / "images" / "dtug-ide.ico")
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def ensure_dir(path: str | Path) -> Path:
    """Ensure directory exists and return its Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_remove(path: str | Path) -> None:
    """Safely remove a file or directory, ignoring errors."""
    p = Path(path)
    if not p.exists():
        return
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    except Exception:
        pass


def clean_temp_dir(temp_dir: str | Path) -> None:
    """Clean all contents inside a temporary directory."""
    p = Path(temp_dir)
    if not p.exists():
        return
    for item in p.iterdir():
        safe_remove(item)


def kill_process_tree(pid: int) -> None:
    """Kill a process and all its children."""
    try:
        import psutil
    except ImportError:
        # Fallback: try to kill just the main process
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.1)
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        return

    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except Exception:
                pass
        gone, alive = psutil.wait_procs(children, timeout=0.5)
        for child in alive:
            try:
                child.kill()
            except Exception:
                pass
        try:
            parent.terminate()
            parent.wait(timeout=0.5)
        except Exception:
            try:
                parent.kill()
            except Exception:
                pass
    except psutil.NoSuchProcess:
        pass
    except Exception:
        pass


def read_file_text(path: str | Path, encoding: str = "utf-8") -> str:
    """Read file text with fallback encoding."""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding=encoding)
    except UnicodeDecodeError:
        return p.read_text(encoding="gbk", errors="replace")
    except Exception:
        return ""


def write_file_text(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """Write text to file."""
    Path(path).write_text(content, encoding=encoding)


def find_compiler_candidates() -> List[str]:
    """Find available g++ compilers on the system."""
    candidates = ["g++", "g++-13", "g++-12", "g++-11", "g++-10", "clang++"]
    found = []
    for cmd in candidates:
        if shutil.which(cmd):
            found.append(cmd)
    if not found and os.name == "nt":
        # On Windows, try common MinGW paths
        for base in [r"C:\mingw64\bin", r"C:\Program Files\mingw-w64\bin"]:
            for exe in ["g++.exe", "clang++.exe"]:
                p = Path(base) / exe
                if p.exists():
                    found.append(str(p))
    return found if found else ["g++"]


def is_valid_cpp_filename(name: str) -> bool:
    """Check if filename looks like a C++ source file."""
    return any(name.lower().endswith(ext) for ext in [".cpp", ".cxx", ".cc", ".c", ".h", ".hpp"])
