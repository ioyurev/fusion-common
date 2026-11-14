from __future__ import annotations

import argparse
import importlib.resources
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger as log
from PySide6 import QtCore, QtGui, QtWidgets

from fusion_common.splash import ANALYSIS_ICON_BG

_DOCS_CSS = """
body {
  max-width: 940px;
  margin: 28px auto;
  padding: 0 22px;
  font-family: 'Segoe UI', system-ui, sans-serif;
  line-height: 1.65;
  color: #1e293b;
  background-color: #ffffff;
}
h1, h2, h3 { color: #0284c7; margin-top: 1.4em; }
nav#TOC {
  background-color: #f8fafc;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 12px 18px;
  margin-bottom: 24px;
}
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
  nav#TOC, blockquote { background-color: #1e293b; border-color: #334155; }
  blockquote { border-left-color: #38bdf8; }
  th, td { border-color: #334155; }
  th, code, pre { background-color: #1e293b; }
}
"""


@dataclass(frozen=True)
class DocPage:
    """Одна глава руководства пользователя."""

    filename: str
    title: str
    markdown_text: str


def _extract_title(md_text: str, fallback: str) -> str:
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _strip_nav_block(md_text: str) -> str:
    """Удаляет межфайловый блок 'Навигация по руководству:' при сборке единого MANUAL.html с оглавлением."""
    lines = md_text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.strip().startswith("Навигация по руководству:"):
            skipping = True
            continue
        if skipping:
            if line.strip() == "---":
                skipping = False
            continue
        out.append(line)
    return "\n".join(out)


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


def _run_pandoc_html(
    markdown_input: str,
    title: str,
    with_toc: bool = False,
) -> str:
    if shutil.which("pandoc") is None:
        raise RuntimeError("Утилита 'pandoc' не найдена в PATH для сборки HTML")
    with tempfile.TemporaryDirectory() as tmp_d:
        css_file = Path(tmp_d) / "style.css"
        css_file.write_text(_DOCS_CSS, encoding="utf-8")
        cmd = [
            "pandoc",
            "--from=markdown",
            "--to=html5",
            "--standalone",
            "--mathml",
            "--embed-resources",
            f"--css={css_file}",
            f"--metadata=title:{title}",
        ]
        if with_toc:
            cmd.extend(["--toc", "--toc-depth=2"])
        proc = subprocess.run(
            cmd,
            input=markdown_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        return proc.stdout.decode("utf-8")


def build_dist_docs_html(
    package_name: str,
    title: str,
    out_dir: Path,
) -> tuple[Path, Path]:
    """Однократно собирает MANUAL.html и LICENSE.html через pandoc на этапе build.cmd."""
    pages = load_package_docs(package_name)
    if not pages:
        raise FileNotFoundError(f"Не найдены .md главы в пакете '{package_name}'")

    out_dir.mkdir(parents=True, exist_ok=True)
    merged_docs = "\n\n---\n\n".join(_strip_nav_block(p.markdown_text) for p in pages)
    manual_html = _run_pandoc_html(merged_docs, title=title, with_toc=True)
    manual_path = out_dir / "MANUAL.html"
    manual_path.write_text(manual_html, encoding="utf-8")

    assets = importlib.resources.files("fusion_common").joinpath("assets")
    ru_md = assets.joinpath("LICENSE.ru.md").read_text(encoding="utf-8")
    en_md = assets.joinpath("LICENSE.md").read_text(encoding="utf-8")
    lic_html = _run_pandoc_html(
        f"{ru_md}\n\n---\n\n{en_md}\n",
        title="GNU General Public License v3.0",
        with_toc=False,
    )
    lic_path = out_dir / "LICENSE.html"
    lic_path.write_text(lic_html, encoding="utf-8")
    return manual_path, lic_path


def _resolve_prebuilt_html(filename: str, package_name: str) -> Path | None:
    """Ищет готовый файл MANUAL.html / LICENSE.html рядом с .exe или в папке проекта (без вызова pandoc)."""
    exe_dir = Path(sys.executable).resolve().parent
    app_slug = package_name.replace("_", "-")
    candidates = [
        exe_dir / filename,
        Path.cwd() / filename,
        Path.cwd() / "pyinstaller" / "dist" / app_slug / filename,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def show_fusion_docs(
    app_title: str,
    package_name: str,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    initial_file: str | None = None,
    parent: QtWidgets.QWidget | None = None,
) -> None:
    """Открывает готовый файл MANUAL.html рядом с .exe в системном браузере (без вызова pandoc)."""
    _ = (app_title, icon_bg_color, initial_file)
    target = _resolve_prebuilt_html("MANUAL.html", package_name)
    if target is None:
        log.bind(package=package_name).warning("Prebuilt MANUAL.html not found")
        QtWidgets.QMessageBox.information(
            parent,
            "Руководство пользователя",
            "Собранный файл MANUAL.html не найден рядом с программой.\n"
            f"Для генерации выполните: uv run python -m fusion_common.docs_viewer --package {package_name}",
        )
        return
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))


def show_fusion_license(
    app_title: str,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    parent: QtWidgets.QWidget | None = None,
) -> None:
    """Открывает готовый файл LICENSE.html рядом с .exe в системном браузере (без вызова pandoc)."""
    _ = (app_title, icon_bg_color)
    target = _resolve_prebuilt_html("LICENSE.html", "fusion_common")
    if target is None:
        log.warning("Prebuilt LICENSE.html not found")
        QtWidgets.QMessageBox.information(
            parent,
            "Лицензия GNU GPL v3.0",
            "Собранный файл LICENSE.html не найден рядом с программой.",
        )
        return
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))


def main_docs_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Build MANUAL.html and LICENSE.html via pandoc"
    )
    parser.add_argument("--package", required=True)
    parser.add_argument("--title", default="Руководство пользователя")
    parser.add_argument("--out-dir", default=".")
    args = parser.parse_args()
    build_dist_docs_html(
        package_name=str(args.package),
        title=str(args.title),
        out_dir=Path(args.out_dir),
    )


if __name__ == "__main__":
    main_docs_cli()
