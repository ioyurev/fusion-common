from __future__ import annotations

import argparse
import contextlib
import importlib.resources
import io
import platform
import re
import struct
import sys
import tomllib
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from types import TracebackType

    from loguru import Record

from loguru import logger
from PySide6 import QtCore, QtGui
from PySide6.QtCore import QByteArray, QRectF, Qt, QUrl
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplashScreen,
    QStyle,
    QVBoxLayout,
    QWidget,
)

COMMON_ICON_BG = "#0D9488"
COLLECTION_ICON_BG = "#FD700D"
ANALYSIS_ICON_BG = "#1E293B"


def _read_dta_curves_svg_text() -> str:
    """Читает векторные кривые ДТА из пакетных ресурсов fusion_common (автономно и в .exe)."""
    ref = importlib.resources.files("fusion_common").joinpath("assets/dta_curves.svg")
    return ref.read_text(encoding="utf-8")


def generate_app_icon_svg(bg_color: str, output_path: Path) -> Path:
    """Генерирует готовый файл icon.svg (скруглённый фон bg_color + кривые ДТА из ресурсов)."""
    raw_svg = _read_dta_curves_svg_text()
    bg_rect = (
        f'\n\t<rect x="29" y="29" width="710" height="710" '
        f'rx="184" ry="184" fill="{bg_color}"/>'
    )
    marker = 'height="768">'
    merged = (
        raw_svg.replace(marker, marker + bg_rect, 1) if marker in raw_svg else raw_svg
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(merged, encoding="utf-8")
    return output_path


def get_pyproject_version(root_path: Path) -> str:
    """Читает версию из pyproject.toml через tomllib без фоллбека на хардкод."""
    toml_path = root_path / "pyproject.toml"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    ver = str(data["project"]["version"])
    return f"v{ver}"


@lru_cache(maxsize=64)
def resolve_ui_icon(
    theme_icon: QIcon.ThemeIcon,
    fallback_pixmap: QStyle.StandardPixmap | None = None,
) -> QIcon:
    """Возвращает иконку QIcon.ThemeIcon с гарантированным фоллбеком на QStyle.StandardPixmap (для Windows 10)."""
    ico = QIcon.fromTheme(theme_icon)
    if not ico.isNull():
        return ico
    if fallback_pixmap is not None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            return app.style().standardIcon(fallback_pixmap)
    return ico


def render_app_icon_image(
    bg_color: str = ANALYSIS_ICON_BG,
    size: int = 256,
) -> QImage:
    """Собирает иконку приложения: генерирует скруглённый фон bg_color и накладывает кривые ДТА."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    margin = size * 0.038
    rect_size = size - 2.0 * margin
    radius = size * 0.24
    painter.setBrush(QBrush(QColor(bg_color)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(
        QRectF(margin, margin, rect_size, rect_size), radius, radius
    )

    svg_bytes = QByteArray(_read_dta_curves_svg_text().encode("utf-8"))
    renderer = QSvgRenderer(svg_bytes)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return img


def render_app_icon_pixmap(
    bg_color: str = ANALYSIS_ICON_BG,
    size: int = 128,
) -> QPixmap:
    """Возвращает собранную иконку приложения (фон + кривые ДТА) как QPixmap."""
    return QPixmap.fromImage(render_app_icon_image(bg_color=bg_color, size=size))


def render_svg_pixmap(svg_path: Path, size: int = 128) -> QPixmap:
    """Рендерит внешний .svg (или встроенную иконку при отсутствии файла) в QPixmap."""
    if svg_path.exists():
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer = QSvgRenderer(str(svg_path))
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        return QPixmap.fromImage(img)
    return render_app_icon_pixmap(size=size)


def render_splash_image(
    subtitle: str,
    version_str: str,
    svg_path: Path | None = None,
    title: str = "Fusion",
    status_text: str = "Initializing core...",
    icon_bg_color: str = ANALYSIS_ICON_BG,
) -> QImage:
    """Отрисовывает сплэш-экран 600x300 в объект QImage в памяти."""
    width, height = 600, 300
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0.0, QColor("#0F172A"))
    gradient.setColorAt(1.0, QColor("#1E3A8A"))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, width, height, 15, 15)

    icon_size = 200
    target_rect = QRectF(30, (height - icon_size) / 2, icon_size, icon_size)
    if svg_path is not None and svg_path.exists():
        renderer = QSvgRenderer(str(svg_path))
        renderer.render(painter, target_rect)
    else:
        icon_img = render_app_icon_image(bg_color=icon_bg_color, size=icon_size)
        painter.drawImage(target_rect, icon_img)

    text_x = 260
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Segoe UI", 48, QFont.Weight.Bold))
    painter.drawText(text_x, 130, title)

    painter.setPen(QColor("#BDC3C7"))
    painter.setFont(QFont("Segoe UI", 14))
    painter.drawText(text_x + 5, 160, subtitle)

    painter.setFont(QFont("Consolas", 10))
    version_rect = QRectF(width - 150, 30, 130, 30)
    v_text = version_str if version_str.startswith("v") else f"v{version_str}"
    painter.drawText(
        version_rect,
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        v_text,
    )

    painter.setPen(QColor("white"))
    painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
    painter.drawText(
        QRectF(0, 0, width - 30, height - 20),
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
        status_text,
    )

    painter.end()
    return img


def generate_wizard_bmp_images(
    subtitle: str,
    version_str: str,
    icon_bg_color: str,
    large_out_path: Path,
    small_out_path: Path,
    title: str = "Fusion",
) -> None:
    """Генерирует фирменные 24-битные BMP-изображения (328x628 и 110x110) для мастера Inno Setup 6."""
    w_l, h_l = 328, 628
    large_img = QImage(w_l, h_l, QImage.Format.Format_RGB888)
    p_l = QPainter(large_img)
    p_l.setRenderHint(QPainter.RenderHint.Antialiasing)
    p_l.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    grad = QLinearGradient(0, 0, w_l, h_l)
    grad.setColorAt(0.0, QColor("#0F172A"))
    grad.setColorAt(1.0, QColor("#1E3A8A"))
    p_l.fillRect(0, 0, w_l, h_l, QBrush(grad))

    ico_sz = 180
    icon_img = render_app_icon_image(bg_color=icon_bg_color, size=ico_sz)
    p_l.drawImage(
        QRectF((w_l - ico_sz) / 2, 90, ico_sz, ico_sz),
        icon_img,
    )
    p_l.setPen(QColor("white"))
    p_l.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
    p_l.drawText(
        QRectF(0, 290, w_l, 55),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        title,
    )
    p_l.setPen(QColor("#CBD5E1"))
    p_l.setFont(QFont("Segoe UI", 11))
    p_l.drawText(
        QRectF(16, 350, w_l - 32, 48),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        subtitle,
    )
    v_text = version_str if version_str.startswith("v") else f"v{version_str}"
    p_l.setFont(QFont("Consolas", 10))
    p_l.drawText(
        QRectF(0, h_l - 44, w_l, 28),
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        v_text,
    )
    p_l.end()
    large_out_path.parent.mkdir(parents=True, exist_ok=True)
    large_img.save(str(large_out_path), "BMP")  # type: ignore[call-overload,arg-type]

    sz_s = 110
    small_img = QImage(sz_s, sz_s, QImage.Format.Format_RGB888)
    small_img.fill(QColor("#FFFFFF"))
    p_s = QPainter(small_img)
    p_s.setRenderHint(QPainter.RenderHint.Antialiasing)
    ico_s = render_app_icon_image(bg_color=icon_bg_color, size=98)
    p_s.drawImage(QRectF(6, 6, 98, 98), ico_s)
    p_s.end()
    small_out_path.parent.mkdir(parents=True, exist_ok=True)
    small_img.save(str(small_out_path), "BMP")  # type: ignore[call-overload,arg-type]


def generate_splash_png(
    svg_path: Path | None,
    output_path: Path,
    version_str: str,
    subtitle: str,
    title: str = "Fusion",
    icon_bg_color: str = ANALYSIS_ICON_BG,
    icon_output_path: Path | None = None,
) -> None:
    """Генерирует и сохраняет splash.png, icon.ico и изображения мастера инсталлятора."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.bind(version=version_str, output=str(output_path)).info(
        "Generating Splash PNG"
    )
    img = render_splash_image(
        subtitle=subtitle,
        version_str=version_str,
        svg_path=svg_path,
        title=title,
        icon_bg_color=icon_bg_color,
    )
    img.save(str(output_path), "PNG")  # type: ignore[call-overload,arg-type]
    generate_wizard_bmp_images(
        subtitle=subtitle,
        version_str=version_str,
        icon_bg_color=icon_bg_color,
        large_out_path=output_path.parent / "wizard_large.bmp",
        small_out_path=output_path.parent / "wizard_small.bmp",
        title=title,
    )
    if icon_output_path is not None:
        icon_output_path.parent.mkdir(parents=True, exist_ok=True)
        icon_img = render_app_icon_image(bg_color=icon_bg_color, size=256)
        if icon_output_path.suffix.lower() == ".ico":
            buf = QByteArray()
            qbuf = QtCore.QBuffer(buf)
            qbuf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
            icon_img.save(qbuf, "PNG")  # type: ignore[call-overload]
            png_bytes = bytes(buf.data())
            ico_hdr = struct.pack(
                "<HHHBBBBHHII", 0, 1, 1, 0, 0, 0, 0, 1, 32, len(png_bytes), 22
            )
            icon_output_path.write_bytes(ico_hdr + png_bytes)
        else:
            icon_img.save(str(icon_output_path), "PNG")  # type: ignore[call-overload,arg-type]


def show_startup_splash(
    app: QApplication,
    subtitle: str,
    version_str: str,
    svg_path: Path | None = None,
    title: str = "Fusion",
    icon_bg_color: str = ANALYSIS_ICON_BG,
) -> QSplashScreen | None:
    """Настраивает стиль (с поддержкой тёмной темы на Windows 10/11), иконку всех окон и сплэш-экран."""
    if app.style().name().lower() == "windowsvista":
        app.setStyle("Fusion")
    app.setWindowIcon(QIcon(render_app_icon_pixmap(icon_bg_color, 64)))
    if getattr(sys, "frozen", False):
        return None
    img = render_splash_image(
        subtitle=subtitle,
        version_str=version_str,
        svg_path=svg_path,
        title=title,
        icon_bg_color=icon_bg_color,
    )
    pixmap = QPixmap.fromImage(img)
    splash = QSplashScreen(
        pixmap,
        Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint,
    )
    splash.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    splash.show()
    app.processEvents()
    return splash


def finish_startup_splash(
    splash: QSplashScreen | None,
    main_window: QWidget,
    min_display_ms: int = 500,
) -> None:
    """Закрывает pyi_splash (в .exe) или QSplashScreen (при запуске из исходников)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        try:
            import pyi_splash  # type: ignore[import-not-found]

            pyi_splash.close()
        except ModuleNotFoundError:
            pass
    if splash is not None:
        QtCore.QTimer.singleShot(
            min_display_ms,
            lambda: splash.finish(main_window),
        )


class FusionAboutDialog(QDialog):
    """Единое окно 'О программе' для приложений экосистемы Fusion."""

    def __init__(
        self,
        app_title: str,
        version_str: str,
        description: str,
        repo_slug: str,
        icon_bg_color: str = ANALYSIS_ICON_BG,
        zenodo_doi: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"О программе {app_title}")
        self.resize(540, 420)
        self.setWindowIcon(QIcon(render_app_icon_pixmap(icon_bg_color, 64)))

        clean_ver = version_str.lstrip("v")
        has_real_doi = bool(zenodo_doi and "XXXX" not in zenodo_doi)
        citation_url = (
            f"https://doi.org/{zenodo_doi}"
            if has_real_doi
            else f"https://github.com/ioyurev/{repo_slug}"
        )
        self.citation_text = (
            f"Yurev, I. O. (2026). {app_title} (Version {clean_ver}) "
            f"[Computer software]. {citation_url}"
        )
        doi_line = (
            f'<br>• DOI (Zenodo): <a href="https://doi.org/{zenodo_doi}">{zenodo_doi}</a>'
            if has_real_doi
            else ""
        )

        layout = QVBoxLayout(self)
        lbl_icon = QLabel()
        lbl_icon.setPixmap(render_app_icon_pixmap(icon_bg_color, 128))
        layout.addWidget(lbl_icon, alignment=Qt.AlignmentFlag.AlignCenter)

        tpl = (
            importlib.resources.files("fusion_common")
            .joinpath("assets/about.html")
            .read_text(encoding="utf-8")
        )
        html_text = tpl.format(
            app_title=app_title,
            version=clean_ver,
            description=description,
            repo_slug=repo_slug,
            doi_line=doi_line,
            citation_text=self.citation_text,
        )
        lbl_body = QLabel(html_text)
        lbl_body.setWordWrap(True)
        lbl_body.setOpenExternalLinks(True)
        lbl_body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(lbl_body)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("Копировать ссылку для цитирования")
        btn_copy.setIcon(
            resolve_ui_icon(
                QIcon.ThemeIcon.EditCopy,
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
            )
        )
        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.citation_text)
        )
        btn_close = QPushButton("Закрыть")
        btn_close.setIcon(
            resolve_ui_icon(
                QIcon.ThemeIcon.WindowClose,
                QStyle.StandardPixmap.SP_DialogCloseButton,
            )
        )
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_copy)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)


def show_fusion_about(
    app_title: str,
    version_str: str,
    description: str,
    repo_slug: str,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    zenodo_doi: str = "",
    parent: QWidget | None = None,
) -> None:
    FusionAboutDialog(
        app_title=app_title,
        version_str=version_str,
        description=description,
        repo_slug=repo_slug,
        icon_bg_color=icon_bg_color,
        zenodo_doi=zenodo_doi,
        parent=parent,
    ).exec()


class _LogMemoryStore(QtCore.QObject):
    line_added = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.lines: deque[str] = deque(maxlen=2000)

    def append(self, line: str) -> None:
        self.lines.append(line)
        with contextlib.suppress(RuntimeError):
            self.line_added.emit(line)

    def clear(self) -> None:
        self.lines.clear()


_LOG_STORE = _LogMemoryStore()


class _ConsoleRedirectStream(io.TextIOBase):
    """Перехватывает запись в stdout/stderr, перенаправляя строки в loguru и оригинальный поток."""

    def __init__(self, orig_stream: TextIO | None, is_err: bool = False) -> None:
        super().__init__()
        self._orig = orig_stream
        self._is_err = is_err
        self._buf = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        if self._orig is not None:
            with contextlib.suppress(OSError, ValueError):
                self._orig.write(s)
                self._orig.flush()
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)  # noqa: PU001
            clean = line.rstrip("\r")
            if clean.strip():
                if self._is_err:
                    logger.opt(depth=1).warning(clean)
                else:
                    logger.opt(depth=1).info(clean)
        return len(s)

    def flush(self) -> None:
        if self._orig is not None:
            with contextlib.suppress(OSError, ValueError):
                self._orig.flush()


def _gui_excepthook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: TracebackType | None,
) -> None:
    """Перехватывает все необработанные исключения, пишет их в лог и показывает QMessageBox в GUI."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.opt(exception=(exc_type, exc_value, exc_tb)).error(
        "Unhandled exception in application"
    )
    app = QApplication.instance()
    if isinstance(app, QApplication):
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Ошибка выполнения")
        box.setText(f"Произошла ошибка:\n{exc_type.__name__}: {exc_value}")
        box.setDetailedText(tb_text)
        box.exec()


def _format_log_record(record: Record) -> str:
    """Форматирует запись loguru с обязательным выводом всех полей extra и полного traceback."""
    extra_dict: dict[str, Any] = record.get("extra", {})  # type: ignore[assignment]
    extra_part = ""
    if extra_dict:
        pairs = ", ".join(f"{k}={extra_dict[k]!r}" for k in sorted(extra_dict))
        extra_part = f" | {pairs}"
    fmt = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} - {message}"
        + extra_part.replace("{", "{{").replace("}", "}}")
        + "\n"
    )
    if record.get("exception") is not None:
        fmt += "{exception}\n"
    return fmt


LOG_SEQ_FILENAME = ".log_seq"
MAX_RETAINED_LOGS = 200
_SEQ_LOG_RE = re.compile(r"^(\d+)_.*\.log$")


@dataclass(frozen=True, order=True)
class _NumberedLogFile:
    seq: int
    path: Path


def _allocate_log_file(
    log_dir: Path,
    *,
    max_retained: int = MAX_RETAINED_LOGS,
) -> Path:
    """Выделяет следующий монотонный порядковый номер лога и чистит старые по номеру."""
    log_dir.mkdir(parents=True, exist_ok=True)
    seq_file = log_dir / LOG_SEQ_FILENAME
    saved_seq = 0
    if seq_file.is_file():
        with contextlib.suppress(OSError, ValueError):
            saved_seq = max(0, int(seq_file.read_text(encoding="utf-8").strip()))

    numbered: list[_NumberedLogFile] = []
    max_existing = 0
    for entry in log_dir.iterdir():
        if not entry.is_file():
            continue
        match = _SEQ_LOG_RE.match(entry.name)
        if match:
            num = int(match.group(1))
            numbered.append(_NumberedLogFile(seq=num, path=entry))
            max_existing = max(max_existing, num)

    next_seq = max(saved_seq, max_existing) + 1
    with contextlib.suppress(OSError):
        tmp_seq = log_dir / f"{LOG_SEQ_FILENAME}.tmp"
        tmp_seq.write_text(str(next_seq), encoding="utf-8")
        tmp_seq.replace(seq_file)

    if max_retained > 0 and len(numbered) >= max_retained:
        excess = len(numbered) - (max_retained - 1)
        for item in sorted(numbered)[:excess]:
            with contextlib.suppress(OSError):
                item.path.unlink()

    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    return log_dir / f"{next_seq:05d}_{stamp}.log"


def setup_fusion_logging(appdata_path: Path) -> Path:
    """Настраивает запись loguru, перехват stdout/stderr и GUI-показ необработанных исключений."""
    log_dir = appdata_path / "logs"
    log_file = _allocate_log_file(log_dir)
    logger.remove()
    orig_err = getattr(sys, "__stderr__", None)
    if orig_err is not None and not getattr(sys, "frozen", False):
        logger.add(
            orig_err,
            level="DEBUG",
            format=_format_log_record,
            backtrace=True,
            diagnose=False,
        )
    logger.add(
        log_file,
        level="DEBUG",
        format=_format_log_record,
        backtrace=True,
        diagnose=False,
        encoding="utf-8",
    )
    logger.add(
        lambda msg: _LOG_STORE.append(str(msg).rstrip("\r\n")),
        level="DEBUG",
        format=_format_log_record,
        backtrace=True,
        diagnose=False,
    )
    if not isinstance(sys.stdout, _ConsoleRedirectStream):
        sys.stdout = _ConsoleRedirectStream(  # type: ignore[assignment]
            getattr(sys, "__stdout__", None), is_err=False
        )
    if not isinstance(sys.stderr, _ConsoleRedirectStream):
        sys.stderr = _ConsoleRedirectStream(  # type: ignore[assignment]
            orig_err, is_err=True
        )
    sys.excepthook = _gui_excepthook
    logger.bind(
        os=platform.platform(),
        python=sys.version.split()[0],  # noqa: MI001
        frozen=bool(getattr(sys, "frozen", False)),
        appdata=str(appdata_path),
        log_file=log_file.name,
    ).info("Application logging initialized")
    return log_dir


class FusionLogViewerDialog(QDialog):
    """Окно просмотра живого журнала работы и вывода консоли внутри GUI."""

    def __init__(
        self,
        app_title: str,
        appdata_path: Path,
        icon_bg_color: str = ANALYSIS_ICON_BG,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Журнал работы — {app_title}")
        self.resize(880, 520)
        self.setWindowIcon(QIcon(render_app_icon_pixmap(icon_bg_color, 64)))
        self.log_dir = appdata_path / "logs"

        layout = QVBoxLayout(self)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Consolas", 10))
        self.text_edit.setPlainText("\n".join(_LOG_STORE.lines))
        self.text_edit.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        layout.addWidget(self.text_edit, 1)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("Копировать всё")
        btn_copy.setIcon(
            resolve_ui_icon(
                QIcon.ThemeIcon.EditCopy,
                QStyle.StandardPixmap.SP_FileDialogDetailedView,
            )
        )
        btn_clear = QPushButton("Очистить экран")
        btn_clear.setIcon(
            resolve_ui_icon(
                QIcon.ThemeIcon.EditClear,
                QStyle.StandardPixmap.SP_DialogResetButton,
            )
        )
        btn_folder = QPushButton("Открыть папку логов")
        btn_folder.setIcon(
            resolve_ui_icon(
                QIcon.ThemeIcon.FolderOpen,
                QStyle.StandardPixmap.SP_DirOpenIcon,
            )
        )
        btn_close = QPushButton("Закрыть")
        btn_close.setIcon(
            resolve_ui_icon(
                QIcon.ThemeIcon.WindowClose,
                QStyle.StandardPixmap.SP_DialogCloseButton,
            )
        )

        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.text_edit.toPlainText())
        )
        btn_clear.clicked.connect(self._on_clear)
        btn_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_dir)))
        )
        btn_close.clicked.connect(self.accept)

        btn_row.addWidget(btn_copy)
        btn_row.addWidget(btn_clear)
        btn_row.addWidget(btn_folder)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        _LOG_STORE.line_added.connect(self._on_line_added)

    def _on_line_added(self, line: str) -> None:
        self.text_edit.appendPlainText(line)

    def _on_clear(self) -> None:
        _LOG_STORE.clear()
        self.text_edit.clear()


def show_fusion_logs(
    app_title: str,
    appdata_path: Path,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    parent: QWidget | None = None,
) -> None:
    FusionLogViewerDialog(
        app_title=app_title,
        appdata_path=appdata_path,
        icon_bg_color=icon_bg_color,
        parent=parent,
    ).exec()


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Fusion Splash & Icon Generator")
    parser.add_argument("--title", default="Fusion")
    parser.add_argument("--subtitle", default=None)
    parser.add_argument("--svg", default=None)
    parser.add_argument("--bg-color", default=ANALYSIS_ICON_BG)
    parser.add_argument("--svg-out", default=None)
    parser.add_argument("--icon-out", default=None)
    parser.add_argument("--version-from", default=None)
    parser.add_argument("--out", default="splash.png")
    args = parser.parse_args()

    if args.svg_out:
        generate_app_icon_svg(str(args.bg_color), Path(args.svg_out))

    if args.subtitle and args.version_from:
        _app = QApplication.instance() or QApplication(sys.argv)
        ver = get_pyproject_version(Path(args.version_from))
        svg_p = Path(args.svg) if args.svg else None
        icon_p = Path(args.icon_out) if args.icon_out else None
        generate_splash_png(
            svg_path=svg_p,
            output_path=Path(args.out),
            version_str=ver,
            subtitle=args.subtitle,
            title=args.title,
            icon_bg_color=str(args.bg_color),
            icon_output_path=icon_p,
        )


if __name__ == "__main__":
    main_cli()
