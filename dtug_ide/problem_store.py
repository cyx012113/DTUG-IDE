"""题库存储层：本地题目管理（题面 + 样例数据 + 解答）。

目录结构（每题一个目录，全部为纯文件，方便用户手动备份 / 用 Git 管理）::

    <题库根>/
      P1001-两数之和/
        meta.json          # 标题 / 来源 / 标签 / 时限 / 内存 / 时间戳
        description.md     # 题面（Markdown）
        data/1.in 1.out …  # 样例与测试数据（.in/.out 或 .ans 配对）
        sol.cpp            # 解答（由 IDE 保存 / 载入）

设计约束：
  * 存储层**不依赖 Qt**，可单测、可在后台线程调用；
  * 题目 ID（目录名）由标题 slug 化生成，冲突时自动加序号，保证唯一且可读；
  * ``sample_cases`` 与评测器复用同一 ``TestCase`` 结构，直接喂给 JudgeEngine。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

META_FILE = "meta.json"
DESC_FILE = "description.md"
DATA_DIR = "data"
SOLUTION_FILE = "sol.cpp"

_IN_EXTS = (".in",)
_OUT_EXTS = (".out", ".ans")


def default_root() -> Path:
    """题库默认根目录：``<AppData>/DTUG/DTUG IDE/problems``（与 QSettings 同域，
    即 Qt 的 AppDataLocation；本机 PyQt6 为硬依赖，非 Qt 分支仅为防御）。"""
    try:
        from PyQt6.QtCore import QStandardPaths

        loc = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        if loc:
            return Path(loc) / "problems"
    except Exception:
        pass
    import os

    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "DTUG" / "DTUG IDE" / "problems"


def _slugify(text: str) -> str:
    """标题 → 目录名（保留中英文数字，其余折叠为 -）。"""
    s = re.sub(r"\s+", "-", str(text or "").strip())
    s = re.sub(r"[^\w\u4e00-\u9fff-]", "", s)
    s = s.strip("-")
    return s[:40] or "problem"


@dataclass
class ProblemInfo:
    """一道题目的元信息。"""

    pid: str
    title: str
    source: str = ""
    tags: List[str] = field(default_factory=list)
    time_limit: float = 1.0
    memory_limit: float = 256.0
    description: str = ""
    created_at: str = ""
    dir: Optional[Path] = None

    # ---- 序列化 ----
    def to_meta(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "title": self.title,
            "source": self.source,
            "tags": list(self.tags),
            "time_limit": self.time_limit,
            "memory_limit": self.memory_limit,
            "created_at": self.created_at,
        }

    @classmethod
    def from_meta(cls, pid: str, meta: Dict[str, Any], pdir: Path) -> "ProblemInfo":
        return cls(
            pid=pid,
            title=str(meta.get("title") or pid),
            source=str(meta.get("source") or ""),
            tags=[str(t) for t in (meta.get("tags") or [])],
            time_limit=float(meta.get("time_limit") or 1.0),
            memory_limit=float(meta.get("memory_limit") or 256.0),
            created_at=str(meta.get("created_at") or ""),
            dir=pdir,
        )

    # ---- 派生路径 ----
    def data_dir(self) -> Path:
        return (self.dir or Path(".")) / DATA_DIR

    def solution_path(self) -> Path:
        return (self.dir or Path(".")) / SOLUTION_FILE

    def description_path(self) -> Path:
        return (self.dir or Path(".")) / DESC_FILE


class ProblemStore:
    """题库的增删查改（纯文件实现，无数据库）。"""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else default_root()

    # ------------------------------------------------------------------
    # 题目集合
    # ------------------------------------------------------------------
    def list_problems(self) -> List[ProblemInfo]:
        """扫描题库根目录，按创建时间倒序返回（目录损坏的跳过）。"""
        out: List[ProblemInfo] = []
        if not self.root.is_dir():
            return out
        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            meta_file = d / META_FILE
            if not meta_file.is_file():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                info = ProblemInfo.from_meta(d.name, meta, d)
                try:
                    info.description = (d / DESC_FILE).read_text(encoding="utf-8")
                except Exception:
                    info.description = ""
                out.append(info)
            except Exception:
                continue  # 单个题目损坏不影响整体列表
        out.sort(key=lambda p: p.created_at or "", reverse=True)
        return out

    def get(self, pid: str) -> Optional[ProblemInfo]:
        d = self.root / pid
        meta_file = d / META_FILE
        if not meta_file.is_file():
            return None
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            info = ProblemInfo.from_meta(pid, meta, d)
            try:
                info.description = (d / DESC_FILE).read_text(encoding="utf-8")
            except Exception:
                pass
            return info
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 增删改
    # ------------------------------------------------------------------
    def create(
        self,
        title: str,
        *,
        source: str = "",
        tags: Optional[List[str]] = None,
        time_limit: float = 1.0,
        memory_limit: float = 256.0,
        description: str = "",
    ) -> ProblemInfo:
        self.root.mkdir(parents=True, exist_ok=True)
        pid = self._unique_pid(title)
        pdir = self.root / pid
        pdir.mkdir(parents=True)
        (pdir / DATA_DIR).mkdir(exist_ok=True)
        info = ProblemInfo(
            pid=pid,
            title=title.strip() or pid,
            source=source.strip(),
            tags=[t.strip() for t in (tags or []) if t.strip()],
            time_limit=float(time_limit),
            memory_limit=float(memory_limit),
            description=description or "",
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            dir=pdir,
        )
        self._save(info)
        return info

    def update(self, info: ProblemInfo) -> bool:
        if not info.dir or not info.dir.is_dir():
            return False
        self._save(info)
        return True

    def delete(self, pid: str) -> bool:
        import shutil

        d = self.root / pid
        if not d.is_dir():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True

    def _save(self, info: ProblemInfo) -> None:
        assert info.dir is not None
        (info.dir / META_FILE).write_text(
            json.dumps(info.to_meta(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (info.dir / DESC_FILE).write_text(info.description or "", encoding="utf-8")

    def _unique_pid(self, title: str) -> str:
        base = _slugify(title)
        pid = base
        n = 1
        while (self.root / pid).exists():
            n += 1
            pid = f"{base}_{n}"
        return pid

    # ------------------------------------------------------------------
    # 数据 / 解答
    # ------------------------------------------------------------------
    def data_files(self, info: ProblemInfo) -> List[Path]:
        d = info.data_dir()
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if p.is_file())

    def sample_cases(self, info: ProblemInfo):
        """枚举 data/ 下的 .in/.out（或 .ans）配对，返回 ``TestCase`` 列表。

        与主窗口「导入测试文件夹」同一套配对规则（自然序排序），缺失一侧的忽略。
        """
        from .judge import TestCase

        d = info.data_dir()
        if not d.is_dir():
            return []

        def natural_key(s: str):
            return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

        file_map: Dict[str, Dict[str, Path]] = {}
        for p in sorted(d.iterdir(), key=lambda p: natural_key(p.name)):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            base = p.name[: -len(p.suffix)]
            if ext in _IN_EXTS:
                file_map.setdefault(base, {})["input"] = p
            elif ext in _OUT_EXTS:
                file_map.setdefault(base, {})["expected"] = p

        cases = []
        for base in sorted(file_map.keys(), key=natural_key):
            pair = file_map[base]
            if "input" in pair and "expected" in pair:
                try:
                    cases.append(
                        TestCase(
                            name=f"{info.pid}:{base}",
                            input=pair["input"].read_text(encoding="utf-8", errors="replace"),
                            expected=pair["expected"].read_text(encoding="utf-8", errors="replace"),
                        )
                    )
                except Exception:
                    continue
        return cases

    def save_solution(self, info: ProblemInfo, code: str) -> Path:
        path = info.solution_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code or "", encoding="utf-8")
        return path

    def load_solution(self, info: ProblemInfo) -> str:
        try:
            return info.solution_path().read_text(encoding="utf-8")
        except Exception:
            return ""

    def open_dir(self, info: ProblemInfo) -> Path:
        return info.dir or self.root


__all__ = ["ProblemStore", "ProblemInfo", "default_root"]
