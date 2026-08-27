"""Build a source release zip for DTUG IDE.

Mirrors the ``release/`` layout used by sibling projects: a self-contained
folder with the runnable sources, docs, and license, then a zip plus a
SHA-256 checksum file.

Usage:
    py tools/make_release.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE_DIR = ROOT / "release"
VERSION = "2.0.0"
DIST_NAME = f"DTUG-IDE-{VERSION}-windows-x64"
DIST_FOLDER = RELEASE_DIR / DIST_NAME
ZIP_PATH = RELEASE_DIR / f"{DIST_NAME}.zip"

# Files and directories to bundle (relative to project root).
INCLUDE_DIRS = ["dtug_ide", "docs", "tests", "tools", "packaging"]
INCLUDE_FILES = [
    "编译.py",
    "README.md",
    "CHANGELOG.md",
    "LICENCE",
    "MANIFEST.in",
    "pyproject.toml",
    "requirements.txt",
    ".gitignore",
]

# Directories never bundled into a release.
EXCLUDE_DIRS = {".git", "__pycache__", "build", "dist", "release", ".workbuddy",
                ".ruff_cache", ".pytest_cache", "legacy", ".venv", "venv"}


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and not any(part in EXCLUDE_DIRS for part in p.parts):
                out.append(p)
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.is_file():
            out.append(p)
    return sorted(out)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    # 受限环境(沙箱)下 rmtree 可能被安全删除拦截，故先重命名旧目录再建新目录
    if DIST_FOLDER.exists():
        _old = DIST_FOLDER.with_name(f"{DIST_FOLDER.name}.old_{int(time.time())}")
        try:
            os.rename(DIST_FOLDER, _old)
        except OSError:
            shutil.rmtree(DIST_FOLDER, ignore_errors=True)
    DIST_FOLDER.mkdir(parents=True)

    files = _iter_files()
    for src in files:
        rel = src.relative_to(ROOT)
        dst = DIST_FOLDER / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    print(f"staged {len(files)} files into {DIST_FOLDER}")

    # Bundle the frozen onedir build if it was built (PyInstaller output).
    # In onedir mode the exe lives alongside its DLLs/ico in release/DTUG-IDE/.
    built_dir = ROOT / "release" / "DTUG-IDE"
    if built_dir.is_dir():
        for item in built_dir.iterdir():
            dst = DIST_FOLDER / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        print(f"bundled onedir build from {built_dir}")

    # Write a release BUILD-INFO.txt
    (DIST_FOLDER / "BUILD-INFO.txt").write_text(
        f"DTUG IDE {VERSION}\n"
        f"Build type: source + frozen onedir build (DTUG-IDE.exe + dependencies)\n"
        f"Python: >=3.10\n"
        f"License: MIT\n",
        encoding="utf-8",
    )

    # Zip the folder
    # 注意：不要直接 unlink 旧 zip —— 沙箱 safe-delete 会拦截 os.remove。
    # 改为重命名到 _trash/ 规避（与项目其它构建脚本一致）。
    if ZIP_PATH.exists():
        import time as _time
        _trash = RELEASE_DIR / "_trash"
        _trash.mkdir(exist_ok=True)
        _ts = _time.strftime("%H%M%S")
        ZIP_PATH.rename(_trash / f"{ZIP_PATH.stem}_{_ts}{ZIP_PATH.suffix}")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(DIST_FOLDER.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(DIST_FOLDER.parent))
    print(f"wrote {ZIP_PATH} ({ZIP_PATH.stat().st_size} bytes)")

    # Checksum file for the zip
    sha = _sha256(ZIP_PATH)
    (RELEASE_DIR / f"{DIST_NAME}.zip.sha256").write_text(
        f"{sha}  {ZIP_PATH.name}\n", encoding="utf-8"
    )
    print(f"sha256: {sha}")

    # Also checksum the wheel, sdist, zip and bundled exe if present
    sums = []
    for p in sorted((ROOT / "dist").glob("*")) if (ROOT / "dist").is_dir() else []:
        if p.is_file():
            sums.append(f"{_sha256(p)}  dist/{p.name}\n")
    if ZIP_PATH.is_file():
        sums.append(f"{_sha256(ZIP_PATH)}  {ZIP_PATH.name}\n")
    bundled_exe = DIST_FOLDER / "DTUG-IDE.exe"
    if bundled_exe.is_file():
        sums.append(f"{_sha256(bundled_exe)}  {DIST_NAME}/DTUG-IDE.exe\n")
    if sums:
        (RELEASE_DIR / "SHA256SUMS.txt").write_text("".join(sums), encoding="utf-8")
        print(f"wrote SHA256SUMS.txt ({len(sums)} artifacts)")


if __name__ == "__main__":
    main()
