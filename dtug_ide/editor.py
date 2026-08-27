"""C++ code editor with syntax highlighting and line numbers."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRect, QRegularExpression
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics,
    QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QResizeEvent,
    QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument,
    QTextFormat, QTextOption, QWheelEvent,
)
from PyQt6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget


def _iter_matches(iterator: "QRegularExpressionMatchIterator"):
    """PyQt6 的 QRegularExpressionMatchIterator 不可直接迭代，
    必须显式使用 hasNext()/next() 遍历。"""
    while iterator.hasNext():
        yield iterator.next()


class CppHighlighter(QSyntaxHighlighter):
    """C/C++ syntax highlighter."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._formats: dict[str, QTextCharFormat] = {}
        self._build_formats()
        self._build_rules()

    def _build_formats(self) -> None:
        def fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            if italic:
                f.setFontItalic(True)
            return f

        self._formats["keyword"] = fmt("#f92672", bold=True)
        self._formats["type"] = fmt("#66d9ef", bold=True)
        self._formats["number"] = fmt("#ae81ff")
        self._formats["string"] = fmt("#e6db74")
        self._formats["char"] = fmt("#e6db74")
        self._formats["comment"] = fmt("#75715e", italic=True)
        self._formats["preproc"] = fmt("#f92672", bold=True)
        self._formats["function"] = fmt("#a6e22e")

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
                # Check if inside string
                quote_count = text.count('"', i, start_idx)
                # Simple heuristic: if odd quotes, might be inside string; skip
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
            # Don't override multi-line comment formatting
            fmt = self.format(match.capturedStart())
            if fmt.foreground().color() != QColor("#75715e"):
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

    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return self._editor.line_number_area_width(), 0

    def paintEvent(self, event: QPaintEvent) -> None:
        self._editor.line_number_area_paint_event(event)


class CodeEditor(QPlainTextEdit):
    """C++ code editor with line numbers and syntax highlighting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._line_number_area = LineNumberArea(self)
        self._highlighter = CppHighlighter(self.document())

        families = QFontDatabase.families()
        if "JetBrains Mono" in families:
            font = QFont("JetBrains Mono", 12)
        elif "Fira Code" in families:
            font = QFont("Fira Code", 12)
        elif "Consolas" in families:
            font = QFont("Consolas", 12)
        else:
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            font.setPointSize(12)
        self.setFont(font)

        self.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * 4)

        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)

        self._update_line_number_area_width(0)
        self._highlight_current_line()

        # Dark theme colors
        self.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0d1117;
                color: #e8edf5;
                border: 1px solid #2a3a5a;
                border-radius: 8px;
                padding: 4px;
                selection-background-color: rgba(79, 156, 247, 0.3);
            }
        """)

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
        painter.fillRect(event.rect(), QColor("#0d1117"))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#5a7a9a"))
                painter.drawText(
                    0, top, self._line_number_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, number,
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    def _highlight_current_line(self) -> None:
        extra_selections = []
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor("#1a2332")
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Auto-indent: maintain indent on Enter
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            cursor = self.textCursor()
            block = cursor.block().text()
            indent = ""
            for ch in block:
                if ch in " \t":
                    indent += ch
                else:
                    break
            # If line ends with '{', add extra indent
            stripped = block.rstrip()
            extra = "    " if stripped.endswith("{") else ""
            super().keyPressEvent(event)
            self.textCursor().insertText(indent + extra)
            return
        # Auto-close brackets
        pairs = {"(": ")", "[": "]", "{": "}", "\"": "\"", "'": "'"}
        if event.text() in pairs:
            super().keyPressEvent(event)
            self.textCursor().insertText(pairs[event.text()])
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
            self.setTextCursor(cursor)
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            font = self.font()
            size = font.pointSize()
            if delta > 0:
                size = min(40, size + 1)
            else:
                size = max(8, size - 1)
            font.setPointSize(size)
            self.setFont(font)
            self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * 4)
            event.accept()
            return
        super().wheelEvent(event)
