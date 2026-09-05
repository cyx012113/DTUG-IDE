"""C++ code editor with syntax highlighting and line numbers.

字体族 / 字号 / 配色均来自 dtug_ide.theme，可通过「设置」实时调整并持久化。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRect, QRegularExpression
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics,
    QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QResizeEvent,
    QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument,
    QTextFormat, QTextOption, QWheelEvent,
)
from PyQt6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from .theme import editor_palette, get_editor_font, monospace_fallbacks


def _iter_matches(iterator: "QRegularExpressionMatchIterator"):
    """PyQt6 的 QRegularExpressionMatchIterator 不可直接迭代，必须显式遍历。"""
    while iterator.hasNext():
        yield iterator.next()


def _resolve_font(family: str, size: int) -> QFont:
    """按首选字体族解析等宽字体；若系统无该字体则回退到系统等宽字体。"""
    available = set(QFontDatabase.families())
    if family in available:
        return QFont(family, size)
    for cand in monospace_fallbacks():
        if cand in available:
            return QFont(cand, size)
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(size)
    return font


class CppHighlighter(QSyntaxHighlighter):
    """C/C++ syntax highlighter，配色由主题调色板驱动。"""

    def __init__(self, document: QTextDocument, palette: dict | None = None) -> None:
        super().__init__(document)
        self._palette = palette or editor_palette()
        self._formats: dict[str, QTextCharFormat] = {}
        self._build_formats()
        self._build_rules()

    def set_palette(self, palette: dict) -> None:
        self._palette = palette
        self._build_formats()
        self.rehighlight()

    def _build_formats(self) -> None:
        p = self._palette

        def fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            if italic:
                f.setFontItalic(True)
            return f

        self._formats["keyword"] = fmt(p["keyword"], bold=True)
        self._formats["type"] = fmt(p["type"], bold=True)
        self._formats["number"] = fmt(p["number"])
        self._formats["string"] = fmt(p["string"])
        self._formats["char"] = fmt(p["string"])
        self._formats["comment"] = fmt(p["comment"], italic=True)
        self._formats["preproc"] = fmt(p["preproc"], bold=True)
        self._formats["function"] = fmt(p["function"])

    def _build_rules(self) -> None:
        keywords = [
            "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor",
            "bool", "break", "case", "catch", "char", "char8_t", "char16_t", "char32_t",
            "class", "compl", "concept", "const", "consteval", "constexpr", "constinit",
            "const_cast", "continue", "co_await", "co_return", "co_yield", "decltype",
            "default", "delete", "do", "double", "dynamic_cast", "else", "enum",
            "explicit", "export", "extern", "false", "float", "for", "friend", "goto",
            "if", "inline", "int", "long", "mutable", "namespace", "new", "noexcept",
            "not", "not_eq", "nullptr", "operator", "or", "or_eq", "private",
            "protected", "public", "register", "reinterpret_cast", "requires", "return",
            "short", "signed", "sizeof", "static", "static_assert", "static_cast",
            "struct", "switch", "template", "this", "thread_local", "throw", "true",
            "try", "typedef", "typeid", "typename", "union", "unsigned", "using",
            "virtual", "void", "volatile", "wchar_t", "while", "xor", "xor_eq",
            "override", "final", "NULL",
        ]
        types = [
            "size_t", "ssize_t", "ptrdiff_t", "intptr_t", "uintptr_t",
            "int8_t", "int16_t", "int32_t", "int64_t",
            "uint8_t", "uint16_t", "uint32_t", "uint64_t",
            "string", "vector", "map", "set", "queue", "stack", "deque",
            "pair", "tuple", "array", "list", "forward_list",
            "unordered_map", "unordered_set", "priority_queue",
            "istream", "ostream", "ifstream", "ofstream", "stringstream",
            "cin", "cout", "cerr", "clog", "endl", "wstring",
        ]
        preproc = [
            "include", "define", "ifdef", "ifndef", "endif", "if", "else", "elif",
            "pragma", "error", "warning", "undef", "line",
        ]

        self._keyword_re = QRegularExpression(r"\b(" + "|".join(keywords) + r")\b")
        self._type_re = QRegularExpression(r"\b(" + "|".join(types) + r")\b")
        self._preproc_re = QRegularExpression(r"^\s*#\s*(" + "|".join(preproc) + r")\b")
        self._number_re = QRegularExpression(
            r"\b(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[0-7]*|[1-9]\d*|\d+)"
            r"(?:[uU][lL]?[lL]?|[lL][lL]?[uU]?)?\b"
            r"|\b\d+\.\d*(?:[eE][+-]?\d+)?\b"
            r"|\b\.\d+(?:[eE][+-]?\d+)?\b"
        )
        self._function_re = QRegularExpression(r"\b([a-zA-Z_]\w*)\s*(?=[\(])")

    def highlightBlock(self, text: str) -> None:
        p = self._palette
        comment_color = QColor(p["comment"])
        # Multi-line comments state
        start_state = 1
        in_comment = self.previousBlockState() == start_state

        # Handle multi-line comments /* */
        i = 0
        while i < len(text):
            if in_comment:
                end_idx = text.find("*/", i)
                if end_idx == -1:
                    self.setFormat(i, len(text) - i, self._formats["comment"])
                    self.setCurrentBlockState(start_state)
                    break
                else:
                    self.setFormat(i, end_idx - i + 2, self._formats["comment"])
                    i = end_idx + 2
                    in_comment = False
            else:
                start_idx = text.find("/*", i)
                if start_idx == -1:
                    break
                quote_count = text.count('"', i, start_idx)
                if quote_count % 2 == 1:
                    i = start_idx + 2
                    continue
                i = start_idx + 2
                in_comment = True

        if in_comment:
            self.setCurrentBlockState(start_state)
        else:
            self.setCurrentBlockState(0)

        # Single-line comment
        for match in _iter_matches(QRegularExpression(r"//.*$").globalMatch(text)):
            fmt = self.format(match.capturedStart())
            if fmt.foreground().color() != comment_color:
                self.setFormat(match.capturedStart(), match.capturedLength(), self._formats["comment"])

        # Preprocessor
        for match in _iter_matches(self._preproc_re.globalMatch(text)):
            self.setFormat(match.capturedStart(), match.capturedLength(), self._formats["preproc"])

        # Keywords
        for match in _iter_matches(self._keyword_re.globalMatch(text)):
            self.setFormat(match.capturedStart(), match.capturedLength(), self._formats["keyword"])

        # Types
        for match in _iter_matches(self._type_re.globalMatch(text)):
            self.setFormat(match.capturedStart(), match.capturedLength(), self._formats["type"])

        # Numbers
        for match in _iter_matches(self._number_re.globalMatch(text)):
            self.setFormat(match.capturedStart(), match.capturedLength(), self._formats["number"])

        # Functions
        for match in _iter_matches(self._function_re.globalMatch(text)):
            self.setFormat(match.capturedStart(), match.capturedLength(), self._formats["function"])

        # Strings (double quote)
        for match in _iter_matches(QRegularExpression(r'"(?:\\.|[^"\\])*"').globalMatch(text)):
            self.setFormat(match.capturedStart(), match.capturedLength(), self._formats["string"])

        # Chars (single quote)
        for match in _iter_matches(QRegularExpression(r"'(?:\\.|[^'\\])'").globalMatch(text)):
            self.setFormat(match.capturedStart(), match.capturedLength(), self._formats["char"])


class LineNumberArea(QWidget):
    """Line number display widget."""

    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return self._editor.line_number_area_width(), 0

    def paintEvent(self, event: QPaintEvent) -> None:
        self._editor.line_number_area_paint_event(event)


# 括号配对（用于匹配高亮 / 一键删除 / 包裹选中）
_BRACKET_OPEN = {"(": ")", "[": "]", "{": "}"}
_BRACKET_CLOSE = {v: k for k, v in _BRACKET_OPEN.items()}


class CodeEditor(QPlainTextEdit):
    """C++ code editor with line numbers and syntax highlighting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._line_number_area = LineNumberArea(self)

        family, size = get_editor_font()
        self._font = _resolve_font(family, size)
        self.setFont(self._font)

        self._highlighter = CppHighlighter(self.document(), editor_palette())
        self._apply_editor_style()

        self.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.setTabStopDistance(QFontMetrics(self._font).horizontalAdvance(" ") * 4)

        # 缩进单位统一为 4 空格（与 TabStopDistance 一致，避免 tab/空格混排）
        self._indent = "    "
        self._indent_w = 4

        # 括号匹配高亮缓存（textChanged 时置脏，懒重建）
        self._bracket_dirty = True
        self._bracket_match: dict[int, int] = {}
        self._bracket_all: set[int] = set()
        self._hover_pos = -1

        # 文本拖拽移动 + Ctrl 拖动复制（QPlainTextEdit 内建，默认已启用）
        self.setAcceptDrops(True)

        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._on_cursor_moved)
        self.textChanged.connect(self._on_doc_changed)

        self._update_line_number_area_width(0)
        self._update_extra_selections()

    # ---- 主题 / 字体 ----
    def apply_theme(self) -> None:
        """从 theme 重新读取字体与配色并应用（设置变更时调用）。"""
        family, size = get_editor_font()
        self._font = _resolve_font(family, size)
        self.setFont(self._font)
        self.setTabStopDistance(QFontMetrics(self._font).horizontalAdvance(" ") * 4)
        self._highlighter.set_palette(editor_palette())
        self._apply_editor_style()
        self._update_line_number_area_width(0)
        self._update_extra_selections()
        self.viewport().update()

    def _apply_editor_style(self) -> None:
        p = editor_palette()
        self.setStyleSheet(
            f"QPlainTextEdit {{"
            f" background-color: {p['bg']};"
            f" color: {p['fg']};"
            f" border: 1px solid {p['border'] if 'border' in p else '#2a3a5a'};"
            f" border-radius: 8px;"
            f" padding: 4px;"
            f" selection-background-color: {p['selection']};"
            f"}}"
        )
        self._line_no_bg = p["line_no_bg"]
        self._line_no_fg = p["line_no_fg"]
        self._line_hl = p["line_hl"]

    def line_number_area_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_area_width(self, _: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(cr.left(), cr.top(), self.line_number_area_width(), cr.height())

    def line_number_area_paint_event(self, event: QPaintEvent) -> None:
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor(self._line_no_bg))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor(self._line_no_fg))
                painter.drawText(
                    0, top, self._line_number_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, number,
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    def _on_cursor_moved(self) -> None:
        self._hover_pos = -1
        self._update_extra_selections()

    def _on_doc_changed(self) -> None:
        self._bracket_dirty = True

    # ---- 括号匹配 ----
    def _build_bracket_map(self, text: str) -> tuple[dict[int, int], set[int]]:
        """正向扫描（跳过字符串/字符/注释），返回匹配映射与所有括号位置集合。"""
        match: dict[int, int] = {}
        brackets: set[int] = set()
        stack: list[tuple[str, int]] = []
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if c == '"':
                i += 1
                while i < n:
                    if text[i] == "\\":
                        i += 2
                        continue
                    if text[i] == '"':
                        i += 1
                        break
                    i += 1
                continue
            if c == "'":
                i += 1
                while i < n:
                    if text[i] == "\\":
                        i += 2
                        continue
                    if text[i] == "'":
                        i += 1
                        break
                    i += 1
                continue
            if c == "/" and i + 1 < n and text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if c == "/" and i + 1 < n and text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
                continue
            if c in _BRACKET_OPEN:
                brackets.add(i)
                stack.append((c, i))
            elif c in _BRACKET_CLOSE:
                brackets.add(i)
                if stack and stack[-1][0] == _BRACKET_CLOSE[c]:
                    _open_ch, open_i = stack.pop()
                    match[open_i] = i
                    match[i] = open_i
            i += 1
        return match, brackets

    def _ensure_bracket_map(self) -> None:
        if self._bracket_dirty:
            self._bracket_match, self._bracket_all = self._build_bracket_map(self.toPlainText())
            self._bracket_dirty = False

    def _bracket_position(self) -> int:
        """返回光标（或鼠标悬停处）旁的括号位置，无则 -1。"""
        self._ensure_bracket_map()
        pos = self._hover_pos if self._hover_pos >= 0 else self.textCursor().position()
        for p in (pos - 1, pos):
            if p in self._bracket_all:
                return p
        return -1

    def _update_extra_selections(self) -> None:
        extra_selections = []
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor(self._line_hl)
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)

        # 括号匹配：匹配则高亮成对括号，失配则错误色高亮
        pos = self._bracket_position()
        if pos >= 0:
            self._ensure_bracket_map()
            match_pos = self._bracket_match.get(pos)
            if match_pos is not None:
                pal = editor_palette()
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(pal["keyword"]))
                fmt.setBackground(QColor(pal["selection"]))
                fmt.setFontWeight(QFont.Weight.Bold)
                for pp in (pos, match_pos):
                    e = QTextEdit.ExtraSelection()
                    c = QTextCursor(self.document())
                    c.setPosition(pp)
                    c.setPosition(pp + 1, QTextCursor.MoveMode.KeepAnchor)
                    e.cursor = c
                    e.format = fmt
                    extra_selections.append(e)
            else:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor("#ffffff"))
                fmt.setBackground(QColor("#f87171"))
                e = QTextEdit.ExtraSelection()
                c = QTextCursor(self.document())
                c.setPosition(pos)
                c.setPosition(pos + 1, QTextCursor.MoveMode.KeepAnchor)
                e.cursor = c
                e.format = fmt
                extra_selections.append(e)

        self.setExtraSelections(extra_selections)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        self._ensure_bracket_map()
        tc = self.cursorForPosition(event.position().toPoint())
        pos = tc.position()
        new_hover = -1
        for p in (pos - 1, pos):
            if p in self._bracket_all:
                new_hover = p
                break
        if new_hover != self._hover_pos:
            self._hover_pos = new_hover
            self._update_extra_selections()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        text = event.text()

        # Tab / Shift+Tab：整块缩进 / 去缩进
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if key == Qt.Key.Key_Backtab or (mods & Qt.KeyboardModifier.ShiftModifier):
                self._indent_selection(-1)
            elif self.textCursor().hasSelection():
                self._indent_selection(1)
            else:
                self.insertPlainText(self._indent)
            return

        # Backspace / Delete：一次删除一对括号，或一整个缩进
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if self._handle_backspace_delete(key == Qt.Key.Key_Backspace):
                return
            super().keyPressEvent(event)
            return

        # Enter：维持缩进；光标在空 {} 之间时展开为两行并自动缩进
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._handle_enter(event)
            return

        # 右括号 / 引号：下一字符相同则跳过；} 额外自动去缩进；引号自动配对
        if text in ")]}\"'":
            tc = self.textCursor()
            if not tc.hasSelection():
                full = self.toPlainText()
                pos = tc.position()
                if pos < len(full) and full[pos] == text:
                    tc.movePosition(QTextCursor.MoveOperation.NextCharacter)
                    self.setTextCursor(tc)
                    self._maybe_convert_int_main()
                    return
            if text == "}" and self._auto_dedent_brace():
                self.insertPlainText("}")
                self._maybe_convert_int_main()
                return
            if text in "\"'":
                self._handle_bracket(text, text)
                return
            super().keyPressEvent(event)
            self._maybe_convert_int_main()
            return

        # 左括号：自动闭合 / 包裹选中文本
        ctrl_like = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        if text in _BRACKET_OPEN and not (mods & ctrl_like):
            self._handle_bracket(text, _BRACKET_OPEN[text])
            return

        super().keyPressEvent(event)
        self._maybe_convert_int_main()

    # ------------------------------------------------------------------
    # 按键子处理
    # ------------------------------------------------------------------
    def _handle_bracket(self, open_ch: str, close_ch: str) -> None:
        """左括号/引号：有选中则包裹选中文本，否则自动闭合并把光标置于中间。"""
        cursor = self.textCursor()
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            cursor.beginEditBlock()
            cursor.setPosition(end)
            cursor.insertText(close_ch)
            cursor.setPosition(start)
            cursor.insertText(open_ch)
            cursor.endEditBlock()
            cursor.setPosition(start + 1)
            cursor.setPosition(end + 1, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            return
        cursor.insertText(open_ch + close_ch)
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
        self.setTextCursor(cursor)

    def _handle_enter(self, event: QKeyEvent) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        line_text = block.text()
        pos_in_block = cursor.positionInBlock()
        before = line_text[:pos_in_block]
        after = line_text[pos_in_block:]

        def leading(s: str) -> str:
            out = ""
            for ch in s:
                if ch in " \t":
                    out += ch
                else:
                    break
            return out

        indent = leading(before)
        line_indent = leading(line_text)
        if len(indent) < len(line_indent):
            indent = line_indent

        # 光标在空 {} 之间：展开为 { \n <缩进> \n }，光标停在缩进行
        if before.rstrip().endswith("{") and after.lstrip().startswith("}"):
            inner = indent + self._indent
            cursor.beginEditBlock()
            cursor.insertText("\n" + inner + "\n" + indent)
            cursor.movePosition(
                QTextCursor.MoveOperation.Left,
                QTextCursor.MoveMode.MoveAnchor,
                len(indent) + 1,
            )
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return

        extra = self._indent if before.rstrip().endswith("{") else ""
        super().keyPressEvent(event)
        self.insertPlainText(indent + extra)

    def _handle_backspace_delete(self, is_backspace: bool) -> bool:
        """返回 True 表示已处理（删除括号对或一整个缩进）。"""
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        full = self.toPlainText()
        pos = cursor.position()

        # 删除成对括号（空对）
        if is_backspace:
            if pos >= 1 and pos < len(full):
                before, after = full[pos - 1], full[pos]
                if before in _BRACKET_OPEN and _BRACKET_OPEN[before] == after:
                    cursor.beginEditBlock()
                    cursor.deletePreviousChar()
                    cursor.deleteChar()
                    cursor.endEditBlock()
                    self.setTextCursor(cursor)
                    return True
        else:
            if pos < len(full) - 1:
                before, after = full[pos], full[pos + 1]
                if before in _BRACKET_OPEN and _BRACKET_OPEN[before] == after:
                    cursor.beginEditBlock()
                    cursor.deleteChar()
                    cursor.deleteChar()
                    cursor.endEditBlock()
                    self.setTextCursor(cursor)
                    return True

        # 删除一整个缩进（仅 Backspace，且光标前在本行内全是空白）
        if is_backspace:
            block = cursor.block()
            before_text = block.text()[:cursor.positionInBlock()]
            if before_text and before_text.strip() == "":
                if before_text.endswith("\t"):
                    n = 1
                else:
                    n = min(len(before_text) - len(before_text.rstrip(" ")), self._indent_w)
                if n:
                    for _ in range(n):
                        cursor.deletePreviousChar()
                    self.setTextCursor(cursor)
                    return True
        return False

    def _auto_dedent_brace(self) -> bool:
        """输入 } 前，若本行光标前全是空白，则先删去一个缩进单位（自动对齐块）。"""
        cursor = self.textCursor()
        block = cursor.block()
        before = block.text()[:cursor.positionInBlock()]
        if not (before and before.strip() == ""):
            return False
        if before.endswith("\t"):
            n = 1
        else:
            n = min(len(before) - len(before.rstrip(" ")), self._indent_w)
        if n:
            for _ in range(n):
                cursor.deletePreviousChar()
            self.setTextCursor(cursor)
            return True
        return False

    def _indent_selection(self, delta: int) -> None:
        """对选区覆盖的每一行整体缩进（delta>0）或去缩进（delta<0）。"""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return
        doc = self.document()
        start_block = doc.findBlock(cursor.selectionStart())
        end_block = doc.findBlock(cursor.selectionEnd())
        start_bn = start_block.blockNumber()
        end_bn = end_block.blockNumber()
        if end_bn > start_bn and cursor.selectionEnd() == end_block.position():
            end_bn -= 1

        cursor.beginEditBlock()
        for bn in range(start_bn, end_bn + 1):
            block = doc.findBlockByNumber(bn)
            bc = QTextCursor(block)
            bc.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            if delta > 0:
                bc.insertText(self._indent)
            else:
                text = block.text()
                if text.startswith("\t"):
                    n = 1
                else:
                    n = 0
                    while n < len(text) and n < self._indent_w and text[n] == " ":
                        n += 1
                if n:
                    bc.setPosition(block.position())
                    bc.setPosition(block.position() + n, QTextCursor.MoveMode.KeepAnchor)
                    bc.removeSelectedText()
        cursor.endEditBlock()

        # 重新选中缩进后的区块
        new_start = doc.findBlockByNumber(start_bn).position()
        new_end_block = doc.findBlockByNumber(end_bn)
        new_end = new_end_block.position() + len(new_end_block.text())
        sel = QTextCursor(doc)
        sel.setPosition(new_start)
        sel.setPosition(new_end, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(sel)

    def _maybe_convert_int_main(self) -> None:
        """文档含 ``#define int long long`` 时，把 ``int main(`` 自动改为 ``signed main(``。"""
        import re

        text = self.toPlainText()
        if not re.search(r"#define\s+int\s+long\s+long\b", text):
            return
        pattern = re.compile(r"(^|[^\w])(int\s+main)\s*\(")
        m = pattern.search(text)
        if not m:
            return
        line_start = text.rfind("\n", 0, m.start()) + 1
        if "//" in text[line_start:m.start()]:
            return
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        edit.setPosition(m.start(2))
        edit.setPosition(m.end(2), QTextCursor.MoveMode.KeepAnchor)
        edit.insertText("signed main")
        edit.endEditBlock()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            font = self._font
            size = font.pointSize()
            if delta > 0:
                size = min(40, size + 1)
            else:
                size = max(8, size - 1)
            font.setPointSize(size)
            self._font = font
            self.setFont(font)
            self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * 4)
            self._update_line_number_area_width(0)
            event.accept()
            return
        super().wheelEvent(event)
