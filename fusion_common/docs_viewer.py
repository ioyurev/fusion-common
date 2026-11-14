from __future__ import annotations

import importlib.resources
import re
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from fusion_common.splash import ANALYSIS_ICON_BG, render_app_icon_pixmap


@dataclass(frozen=True)
class DocPage:
    """Одна страница встроенного руководства пользователя."""

    filename: str
    title: str
    markdown_text: str


def _extract_title(md_text: str, fallback: str) -> str:
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _format_inline_math(text: str) -> str:
    """Преобразует встроенные формулы LaTeX ($...$) в читаемый HTML с индексами и греческими символами."""
    replacements = (
        (r"\Delta ", "Δ"),
        (r"\Delta", "Δ"),
        (r"\alpha", "α"),
        (r"\beta", "β"),
        (r"\tau", "τ"),
        (r"\sigma", "σ"),
        (r"\pm", "±"),
        (r"\le", "≤"),
        (r"\ge", "≥"),
        (r"\ne", "≠"),
        (r"\approx", "≈"),
        (r"\to", "→"),
        (r"\leftrightarrow", "↔"),
        (r"\cdot", "·"),
        (r"\times", "×"),
        (r"\infty", "∞"),
        (r"^\circ\text{C}", "°C"),
        (r"^\circ", "°"),
    )

    def _convert_match(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        for src, dst in replacements:
            expr = expr.replace(src, dst)
        expr = re.sub(r"\\text\{([^}]*)\}", r"\1", expr)
        expr = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", expr)
        expr = re.sub(r"_\{([^}]*)\}", r"<sub>\1</sub>", expr)
        expr = re.sub(r"_([a-zA-Z0-9])", r"<sub>\1</sub>", expr)
        expr = re.sub(r"\^\{([^}]*)\}", r"<sup>\1</sup>", expr)
        expr = re.sub(r"\^([0-9+-])", r"<sup>\1</sup>", expr)
        return f"<b>{expr}</b>"

    return re.sub(r"\$([^$\n]+)\$", _convert_match, text)


def _apply_browser_theme(
    browser: QtWidgets.QTextBrowser,
    scheme: QtCore.Qt.ColorScheme,
) -> None:
    is_dark = scheme == QtCore.Qt.ColorScheme.Dark
    bg = "#0F172A" if is_dark else "#FFFFFF"
    fg = "#E2E8F0" if is_dark else "#1E293B"
    code_bg = "#1E293B" if is_dark else "#F1F5F9"
    accent = "#38BDF8" if is_dark else "#0284C7"
    browser.setStyleSheet(
        f"QTextBrowser {{ background-color: {bg}; color: {fg}; "
        f"font-family: 'Segoe UI'; font-size: 14px; padding: 14px; }}"
    )
    browser.document().setDefaultStyleSheet(
        f"h1, h2, h3 {{ color: {accent}; margin-top: 14px; }} "
        f"code, pre {{ background-color: {code_bg}; font-family: Consolas; padding: 2px 4px; }} "
        f"blockquote {{ border-left: 3px solid {accent}; margin-left: 8px; padding-left: 8px; }} "
        f"a {{ color: {accent}; }}"
    )


def load_package_docs(package_name: str) -> list[DocPage]:
    """Загружает только главы руководства .md из <package_name>/docs/ (без файлов лицензий)."""
    pages: list[DocPage] = []
    try:
        docs_dir = importlib.resources.files(package_name).joinpath("docs")
        if docs_dir.is_dir():
            items = sorted(
                (p for p in docs_dir.iterdir() if p.name.endswith(".md")),
                key=lambda p: p.name,
            )
            for item in items:
                md_raw = item.read_text(encoding="utf-8")
                pages.append(
                    DocPage(
                        filename=item.name,
                        title=_extract_title(md_raw, item.name),
                        markdown_text=md_raw,
                    )
                )
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        pass
    return pages


class FusionDocsViewerDialog(QtWidgets.QDialog):
    """Двухпанельное окно просмотра руководства пользователя и теоретических основ."""

    def __init__(
        self,
        app_title: str,
        package_name: str,
        icon_bg_color: str = ANALYSIS_ICON_BG,
        initial_file: str | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Руководство пользователя — {app_title}")
        self.resize(980, 660)
        self.setWindowIcon(QtGui.QIcon(render_app_icon_pixmap(icon_bg_color, 64)))
        self.pages = load_package_docs(package_name)
        self._file_to_idx = {p.filename: idx for idx, p in enumerate(self.pages)}

        root_layout = QtWidgets.QVBoxLayout(self)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        self.nav_list = QtWidgets.QListWidget()
        self.nav_list.setMinimumWidth(250)
        self.nav_list.setMaximumWidth(340)
        for p in self.pages:
            self.nav_list.addItem(p.title)
        self.nav_list.currentRowChanged.connect(self._on_row_selected)
        splitter.addWidget(self.nav_list)

        self.browser = QtWidgets.QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._on_anchor_clicked)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root_layout.addWidget(splitter, 1)

        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.addStretch()
        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(btn_close)
        root_layout.addLayout(bottom_row)

        style_hints = QtGui.QGuiApplication.styleHints()
        style_hints.colorSchemeChanged.connect(self._apply_theme)
        self._apply_theme(style_hints.colorScheme())

        start_row = 0
        if initial_file and initial_file in self._file_to_idx:
            start_row = self._file_to_idx[initial_file]
        if self.pages:
            self.nav_list.setCurrentRow(start_row)

    def _apply_theme(self, scheme: QtCore.Qt.ColorScheme) -> None:
        _apply_browser_theme(self.browser, scheme)
        self._on_row_selected(self.nav_list.currentRow())

    def _on_row_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.pages):
            return
        page = self.pages[row]
        self.browser.setMarkdown(_format_inline_math(page.markdown_text))

    def _on_anchor_clicked(self, url: QtCore.QUrl) -> None:
        scheme = url.scheme().lower()
        if scheme in ("http", "https", "mailto"):
            QtGui.QDesktopServices.openUrl(url)
            return
        target_name = url.path().split("/")[-1]  # noqa: MI001
        if target_name in self._file_to_idx:
            self.nav_list.setCurrentRow(self._file_to_idx[target_name])


class FusionLicenseDialog(QtWidgets.QDialog):
    """Отдельное окно просмотра текста лицензии GNU GPL v3.0 (RU / EN)."""

    def __init__(
        self,
        app_title: str,
        icon_bg_color: str = ANALYSIS_ICON_BG,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Лицензия GNU GPL v3.0 — {app_title}")
        self.resize(740, 520)
        self.setWindowIcon(QtGui.QIcon(render_app_icon_pixmap(icon_bg_color, 64)))

        layout = QtWidgets.QVBoxLayout(self)
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(QtWidgets.QLabel("Язык текста:"))
        self.cb_lang = QtWidgets.QComboBox()
        self.cb_lang.addItem("Русский (LICENSE.ru.md)", "LICENSE.ru.md")
        self.cb_lang.addItem("English (LICENSE.md)", "LICENSE.md")
        self.cb_lang.currentIndexChanged.connect(self._render_license)
        top_row.addWidget(self.cb_lang)
        top_row.addStretch()
        layout.addLayout(top_row)

        self.browser = QtWidgets.QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        layout.addWidget(self.browser, 1)

        bottom_row = QtWidgets.QHBoxLayout()
        lbl_gpl = QtWidgets.QLabel(
            "Официальная страница лицензии GNU GPL v3.0: "
            "<a href='https://www.gnu.org/licenses/gpl-3.0.ru.html'>gnu.org/licenses/gpl-3.0.ru.html</a>"
        )
        lbl_gpl.setOpenExternalLinks(True)
        btn_close = QtWidgets.QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(lbl_gpl)
        bottom_row.addStretch()
        bottom_row.addWidget(btn_close)
        layout.addLayout(bottom_row)

        style_hints = QtGui.QGuiApplication.styleHints()
        style_hints.colorSchemeChanged.connect(self._apply_theme)
        self._apply_theme(style_hints.colorScheme())

    def _apply_theme(self, scheme: QtCore.Qt.ColorScheme) -> None:
        _apply_browser_theme(self.browser, scheme)
        self._render_license()

    def _render_license(self) -> None:
        fname = str(self.cb_lang.currentData() or "LICENSE.ru.md")
        assets = importlib.resources.files("fusion_common").joinpath("assets")
        try:
            text = assets.joinpath(fname).read_text(encoding="utf-8")
            self.browser.setMarkdown(text)
        except (FileNotFoundError, OSError):
            self.browser.setPlainText("Файл лицензии не найден.")


def show_fusion_docs(
    app_title: str,
    package_name: str,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    initial_file: str | None = None,
    parent: QtWidgets.QWidget | None = None,
) -> None:
    FusionDocsViewerDialog(
        app_title=app_title,
        package_name=package_name,
        icon_bg_color=icon_bg_color,
        initial_file=initial_file,
        parent=parent,
    ).exec()


def show_fusion_license(
    app_title: str,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    parent: QtWidgets.QWidget | None = None,
) -> None:
    FusionLicenseDialog(
        app_title=app_title,
        icon_bg_color=icon_bg_color,
        parent=parent,
    ).exec()
