from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from loguru import logger
from PySide6 import QtCore
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QSplashScreen, QWidget

COLLECTION_ICON_BG = "#FD700D"
ANALYSIS_ICON_BG = "#1E293B"
DTA_CURVES_SVG_PATH = Path(__file__).resolve().parent / "assets" / "dta_curves.svg"


def get_pyproject_version(root_path: Path) -> str:
    """Читает версию из pyproject.toml через tomllib без фоллбека на хардкод."""
    toml_path = root_path / "pyproject.toml"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    ver = str(data["project"]["version"])
    return f"v{ver}"


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

    if DTA_CURVES_SVG_PATH.exists():
        renderer = QSvgRenderer(str(DTA_CURVES_SVG_PATH))
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


def generate_splash_png(
    svg_path: Path | None,
    output_path: Path,
    version_str: str,
    subtitle: str,
    title: str = "Fusion",
    icon_bg_color: str = ANALYSIS_ICON_BG,
    icon_output_path: Path | None = None,
) -> None:
    """Генерирует и сохраняет splash.png (и опционально icon.png) для упаковки PyInstaller."""
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
    if icon_output_path is not None:
        icon_output_path.parent.mkdir(parents=True, exist_ok=True)
        icon_img = render_app_icon_image(bg_color=icon_bg_color, size=256)
        icon_img.save(str(icon_output_path), "PNG")  # type: ignore[call-overload,arg-type]


def show_startup_splash(
    app: QApplication,
    subtitle: str,
    version_str: str,
    svg_path: Path | None = None,
    title: str = "Fusion",
    icon_bg_color: str = ANALYSIS_ICON_BG,
) -> QSplashScreen | None:
    """Показывает QSplashScreen при запуске из исходников (вне PyInstaller)."""
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


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Fusion Splash & Icon Generator")
    parser.add_argument("--title", default="Fusion")
    parser.add_argument("--subtitle", required=True)
    parser.add_argument("--svg", default=None)
    parser.add_argument("--bg-color", default=ANALYSIS_ICON_BG)
    parser.add_argument("--icon-out", default=None)
    parser.add_argument("--version-from", required=True)
    parser.add_argument("--out", default="splash.png")
    args = parser.parse_args()

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
