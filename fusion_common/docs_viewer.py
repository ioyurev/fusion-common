from __future__ import annotations

import hashlib
import importlib.resources
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger as log
from PySide6 import QtCore, QtGui, QtWidgets

from fusion_common.packaging import get_appdata_path
from fusion_common.splash import ANALYSIS_ICON_BG

_DOCS_CSS = """
body {
  max-width: 920px;
  margin: 28px auto;
  padding: 0 20px;
  font-family: 'Segoe UI', system-ui, sans-serif;
  line-height: 1.6;
  color: #1e293b;
  background-color: #ffffff;
}
h1, h2, h3 { color: #0284c7; margin-top: 1.3em; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; }
th, td { border: 1px solid #cbd5e1; padding: 8px 10px; text-align: left; }
th { background-color: #f1f5f9; }
code, pre { background-color: #f1f5f9; font-family: Consolas, monospace; padding: 2px 5px; border-radius: 4px; }
pre { padding: 10px; overflow-x: auto; }
blockquote { border-left: 4px solid #0284c7; margin: 12px 0; padding: 6px 14px; background-color: #f8fafc; }
a { color: #0284c7; text-decoration: none; }
a:hover { text-decoration: underline; }
@media (prefers-color-scheme: dark) {
  body { color: #e2e8f0; background-color: #0f172a; }
  h1, h2, h3, a { color: #38bdf8; }
  th, td { border-color: #334155; }
  th, code, pre { background-color: #1e293b; }
  blockquote { border-left-color: #38bdf8; background-color: #1e293b; }
}
"""


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


def load_package_docs(package_name: str) -> list[DocPage]:
    """Загружает все главы руководства .md из <package_name>/docs/."""
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


def _convert_md_to_html_pandoc(md_text: str, title: str, css_path: Path) -> str:
    rewritten = re.sub(r"\]\(([0-9A-Za-z_.-]+)\.md\)", r"](\1.html)", md_text)
    proc = subprocess.run(
        [
            "pandoc",
            "--from=markdown",
            "--to=html5",
            "--standalone",
            "--mathml",
            "--embed-resources",
            f"--css={css_path}",
            f"--metadata=title:{title}",
        ],
        input=rewritten.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    return proc.stdout.decode("utf-8")


def compile_docs_to_html_dir(package_name: str) -> Path:
    """Компилирует главы руководства .md через pandoc --standalone --mathml с кэшированием по хэшу."""
    pages = load_package_docs(package_name)
    if not pages:
        raise FileNotFoundError(
            f"В пакете '{package_name}' не найдены файлы документации"
        )
    if shutil.which("pandoc") is None:
        raise RuntimeError(
            "Утилита 'pandoc' не найдена в PATH для сборки HTML + MathML"
        )

    hasher = hashlib.sha256(_DOCS_CSS.encode("utf-8"))
    for p in pages:
        hasher.update(p.filename.encode("utf-8"))
        hasher.update(p.markdown_text.encode("utf-8"))
    digest = hasher.hexdigest()[:16]

    out_dir = get_appdata_path(package_name.replace("_", "-")) / "docs_html" / digest
    first_html = out_dir / Path(pages[0].filename).with_suffix(".html").name  # noqa: MI001
    if first_html.exists():
        return first_html

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_d:
        css_file = Path(tmp_d) / "style.css"
        css_file.write_text(_DOCS_CSS, encoding="utf-8")
        for p in pages:
            html_name = Path(p.filename).with_suffix(".html").name
            html_str = _convert_md_to_html_pandoc(p.markdown_text, p.title, css_file)
            (out_dir / html_name).write_text(html_str, encoding="utf-8")
    return first_html


def compile_license_to_html() -> Path:
    """Компилирует файлы лицензии LICENSE.ru.md и LICENSE.md через pandoc и возвращает путь к странице."""
    if shutil.which("pandoc") is None:
        raise RuntimeError("Утилита 'pandoc' не найдена в PATH")
    assets = importlib.resources.files("fusion_common").joinpath("assets")
    ru_md = assets.joinpath("LICENSE.ru.md").read_text(encoding="utf-8")
    en_md = assets.joinpath("LICENSE.md").read_text(encoding="utf-8")
    combined_md = f"{ru_md}\n\n---\n\n{en_md}\n"
    digest = hashlib.sha256((_DOCS_CSS + combined_md).encode("utf-8")).hexdigest()[:16]
    out_dir = get_appdata_path("fusion-common") / "license_html" / digest
    out_file = out_dir / "LICENSE.html"
    if not out_file.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp_d:
            css_file = Path(tmp_d) / "style.css"
            css_file.write_text(_DOCS_CSS, encoding="utf-8")
            html_str = _convert_md_to_html_pandoc(
                combined_md, "GNU General Public License v3.0", css_file
            )
            out_file.write_text(html_str, encoding="utf-8")
    return out_file


def show_fusion_docs(
    app_title: str,
    package_name: str,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    initial_file: str | None = None,
    parent: QtWidgets.QWidget | None = None,
) -> None:
    _ = (app_title, icon_bg_color)
    try:
        first_html = compile_docs_to_html_dir(package_name)
        target = (
            first_html.parent / Path(initial_file).with_suffix(".html").name
            if initial_file
            else first_html
        )
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(target if target.exists() else first_html))
        )
    except Exception as exc:  # noqa: BLE001
        log.bind(package=package_name, error=str(exc)).error("Failed to open docs")
        QtWidgets.QMessageBox.warning(parent, "Ошибка открытия документации", str(exc))


def show_fusion_license(
    app_title: str,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    parent: QtWidgets.QWidget | None = None,
) -> None:
    _ = (app_title, icon_bg_color)
    try:
        lic_html = compile_license_to_html()
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(lic_html)))
    except Exception as exc:  # noqa: BLE001
        log.bind(error=str(exc)).error("Failed to open license")
        QtWidgets.QMessageBox.warning(parent, "Ошибка открытия лицензии", str(exc))
