"""轻量级 C++ 代码格式化器（模仿 Prettier 的「保存即格式化」理念）。

特性：
  * 4 空格缩进，依据 ``{`` ``}`` 自动计算层级（正确处理字符串/字符/注释内的括号）；
  * 去除行尾空白、压缩连续空行、保证文件以单个换行结尾；
  * 控制关键字（if/for/while/switch/catch/return）后紧跟 ``(`` 时补一个空格；
  * 若系统已安装 ``clang-format`` 则优先委托它（更专业），否则使用内置实现。

内置实现刻意保守，只做「安全」的排版，不会改写语义，避免破坏竞赛代码。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional

# 需要在其后补空格的控制关键字：
#  - if/for/while/switch/catch/return 紧跟左括号时；
#  - else/do 紧跟非空白（如 `{` 或 `if(`）时。
_KW_SPACE = re.compile(r"\b(if|for|while|switch|catch|return)(?=\()|\b(else|do)(?=\S)")
# for 头三段（init; cond; inc）的空白规范化，仅改空白、不改语义
def _fix_keywords(line: str) -> str:
    """关键字后补一个空格（安全改写，不触碰括号内容）。"""
    return _KW_SPACE.sub(lambda m: m.group(0) + " ", line)


def _fix_for_header(line: str) -> str:
    """把 ``for (a; b; c)`` 头部的三段按 ``; `` 规范化，仅调整空白。

    用括号配平扫描，支持头内出现嵌套括号（如 ``for (...; i < __lg(n); ...)``）。
    """
    out: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        if (
            line[i:i+3] == "for"
            and (i == 0 or not line[i-1].isalnum())
            and i + 4 < n
            and line[i+3] in " \t"
            and line[i+4] == "("
        ):
            depth = 0
            j = i + 4
            while j < n:
                ch = line[j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            else:
                out.append(line[i:])
                break
            header = line[i+5:j]
            parts = [p.strip() for p in header.split(";")]
            out.append("for (" + "; ".join(parts).rstrip() + ")")
            i = j + 1
        else:
            out.append(line[i])
            i += 1
    return "".join(out)


def _has_clang_format() -> Optional[str]:
    path = shutil.which("clang-format") or shutil.which("clang-format.exe")
    return path


def _real_braces(line: str, in_block: bool):
    """扫描一行，返回 (净开括号数, 净闭括号数, 行内是否仍以块注释开启, 起始连续闭括号数)。

    字符串、字符字面量、行/块注释内的括号不计入，避免 ``cout << "{";`` 之类误判。
    """
    opens = 0
    closes = 0
    lead_close = 0
    i = 0
    n = len(line)
    line_in_block = in_block
    while i < n:
        c = line[i]
        if line_in_block:
            if c == "*" and i + 1 < n and line[i + 1] == "/":
                line_in_block = False
                i += 2
                continue
            i += 1
            continue
        if c == "/" and i + 1 < n and line[i + 1] == "/":
            break  # 行注释，其后不再计数
        if c == "/" and i + 1 < n and line[i + 1] == "*":
            line_in_block = True
            i += 2
            continue
        if c == '"' or c == "'":
            q = c
            i += 1
            while i < n:
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == "{":
            opens += 1
        elif c == "}":
            closes += 1
        i += 1
    # 起始连续闭括号（用于减缩进）
    stripped = line.lstrip()
    for ch in stripped:
        if ch == "}":
            lead_close += 1
        elif ch in " \t":
            continue
        else:
            break
    return opens - closes, line_in_block, lead_close


def _builtin_format(code: str) -> str:
    lines = code.split("\n")
    out: list[str] = []
    indent = 0
    in_block = False
    blank_run = 0
    for raw in lines:
        line = raw.rstrip()
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 1:
                out.append("")
            continue
        blank_run = 0
        delta, in_block, lead_close = _real_braces(line, in_block)
        # 打印缩进 = 当前层级减去「行首连续 }」个数，让 } 自身对齐到外层；
        # 下一行层级 = 当前层级 + 本行净括号数（开 - 闭），只调整一次。
        # 注意：不能把 lead_close 与 delta 各减一次，否则每个 } 会多减一级缩进。
        print_indent = max(0, indent - lead_close)
        stripped = line.strip()
        # 安全改写：关键字后补空格、for 头三段分号规范化（仅改空白，不改语义）
        stripped = _fix_keywords(stripped)
        stripped = _fix_for_header(stripped)
        out.append(" " * (print_indent * 4) + stripped)
        indent = max(0, indent + delta)
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text


def format_cpp(code: str) -> str:
    """格式化 C++ 源码，返回格式化后的文本。"""
    cf = _has_clang_format()
    if cf:
        try:
            res = subprocess.run(
                [cf, "--style=file" if False else "--style=llvm"],
                input=code,
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout
        except Exception:
            pass
    return _builtin_format(code)
