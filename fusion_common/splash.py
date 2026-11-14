from __future__ import annotations

import argparse
import importlib.resources
import sys
import tomllib
from pathlib import Path

from loguru import logger
from PySide6 import QtCore
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
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
    QLabel,
    QPushButton,
    QSplashScreen,
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


class FusionAboutDialog(QDialog):
    """Единое окно 'О программе' для приложений экосистемы Fusion."""

    def __init__(
        self,
        app_title: str,
        version_str: str,
        description: str,
        repo_slug: str,
        icon_bg_color: str = ANALYSIS_ICON_BG,
        zenodo_doi: str = "10.5281/zenodo.XXXXXXX",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"О программе {app_title}")
        self.resize(500, 390)
        self.setWindowIcon(QIcon(render_app_icon_pixmap(icon_bg_color, 64)))

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
            version=version_str.lstrip("v"),
            description=description,
            repo_slug=repo_slug,
            zenodo_doi=zenodo_doi,
        )
        lbl_body = QLabel(html_text)
        lbl_body.setWordWrap(True)
        lbl_body.setOpenExternalLinks(True)
        layout.addWidget(lbl_body)

        from fusion_common.docs_viewer import show_fusion_docs

        pkg_name = repo_slug.replace("-", "_")
        btn_docs = QPushButton("Открыть руководство и текст лицензии в программе...")
        btn_docs.clicked.connect(
            lambda: show_fusion_docs(
                app_title=app_title,
                package_name=pkg_name,
                icon_bg_color=icon_bg_color,
                parent=self,
            )
        )
        layout.addWidget(btn_docs)

        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)


def show_fusion_about(
    app_title: str,
    version_str: str,
    description: str,
    repo_slug: str,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    parent: QWidget | None = None,
) -> None:
    FusionAboutDialog(
        app_title=app_title,
        version_str=version_str,
        description=description,
        repo_slug=repo_slug,
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
