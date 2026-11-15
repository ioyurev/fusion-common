from __future__ import annotations

import argparse
import html
import importlib.resources
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger as log
from PySide6 import QtCore, QtGui, QtWidgets

from fusion_common.splash import ANALYSIS_ICON_BG

_DEFAULT_TITLES: dict[str, str] = {
    "fusion_collection": "Fusion Collection — Руководство пользователя",
    "fusion_analysis": "Fusion Analysis — Руководство пользователя",
}

_MANUAL_STYLE_AND_SCRIPT = """
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: 'Segoe UI', system-ui, sans-serif;
  line-height: 1.65;
  color: #1e293b;
  background-color: #ffffff;
  display: flex;
  min-height: 100vh;
}
aside.sidebar {
  width: 300px;
  flex-shrink: 0;
  background-color: #f8fafc;
  border-right: 1px solid #cbd5e1;
  padding: 20px 14px;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}
aside.sidebar h2 {
  font-size: 16px;
  margin: 0 0 14px 0;
  color: #0284c7;
  padding-bottom: 10px;
  border-bottom: 1px solid #cbd5e1;
}
a.nav-link {
  display: block;
  padding: 8px 10px;
  margin-bottom: 4px;
  border-radius: 6px;
  color: #334155;
  text-decoration: none;
  font-size: 14px;
  cursor: pointer;
}
a.nav-link:hover { background-color: #e2e8f0; }
a.nav-link.active {
  background-color: #0284c7;
  color: #ffffff;
  font-weight: 600;
}
main.content {
  flex: 1;
  max-width: 900px;
  padding: 28px 36px;
  margin: 0 auto;
}
section.chapter-page { display: none; }
section.chapter-page.active { display: block; }
h1, h2, h3 { color: #0284c7; margin-top: 1.3em; }
section.chapter-page > h1:first-child { margin-top: 0; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; }
th, td { border: 1px solid #cbd5e1; padding: 8px 10px; text-align: left; }
th { background-color: #f1f5f9; }
code, pre { background-color: #f1f5f9; font-family: Consolas, monospace; padding: 2px 5px; border-radius: 4px; }
pre { padding: 10px; overflow-x: auto; }
blockquote { border-left: 4px solid #0284c7; margin: 12px 0; padding: 6px 14px; background-color: #f8fafc; }
a { color: #0284c7; text-decoration: none; }
a:hover { text-decoration: underline; }
.pager-row {
  display: flex;
  justify-content: space-between;
  margin-top: 32px;
  padding-top: 16px;
  border-top: 1px solid #cbd5e1;
}
button.pager-btn {
  background-color: #f1f5f9;
  color: #0284c7;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 8px 14px;
  font-size: 14px;
  cursor: pointer;
}
button.pager-btn:hover:not(:disabled) { background-color: #e2e8f0; }
button.pager-btn:disabled { opacity: 0.4; cursor: default; }
@media (prefers-color-scheme: dark) {
  body { color: #e2e8f0; background-color: #0f172a; }
  aside.sidebar { background-color: #1e293b; border-right-color: #334155; }
  aside.sidebar h2 { color: #38bdf8; border-bottom-color: #334155; }
  a.nav-link { color: #cbd5e1; }
  a.nav-link:hover { background-color: #334155; }
  a.nav-link.active { background-color: #0284c7; color: #ffffff; }
  h1, h2, h3, a { color: #38bdf8; }
  blockquote { background-color: #1e293b; border-left-color: #38bdf8; }
  th, td, .pager-row { border-color: #334155; }
  th, code, pre, button.pager-btn { background-color: #1e293b; border-color: #334155; color: #38bdf8; }
}
"""

_MANUAL_JS = """
function showChapter(index) {
  const sections = document.querySelectorAll('section.chapter-page');
  const links = document.querySelectorAll('a.nav-link');
  if (index < 0 || index >= sections.length) return;
  sections.forEach((sec, i) => sec.classList.toggle('active', i === index));
  links.forEach((lnk, i) => lnk.classList.toggle('active', i === index));
  window.scrollTo(0, 0);
}
document.addEventListener('DOMContentLoaded', () => {
  document.body.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-chapter-file]');
    if (a) {
      e.preventDefault();
      const fname = a.getAttribute('data-chapter-file');
      const targetSec = document.querySelector(`section.chapter-page[data-filename="${fname}"]`);
      if (targetSec) {
        showChapter(parseInt(targetSec.getAttribute('data-index'), 10));
      }
    }
  });
});
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
    """Удаляет межфайловый блок 'Навигация по руководству:' из текста главы."""
    lines = md_text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Навигация по руководству:"):
            skipping = True
            continue
        if skipping:
            if re.match(r"^[-*_]{3,}$", stripped):
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


def _run_pandoc_fragment(markdown_input: str) -> str:
    """Конвертирует Markdown с формулами LaTeX в HTML5+MathML фрагмент через UTF-8 stdin/stdout."""
    if shutil.which("pandoc") is None:
        raise RuntimeError("Утилита 'pandoc' не найдена в PATH для сборки HTML")
    proc = subprocess.run(
        ["pandoc", "--from=markdown", "--to=html5", "--mathml"],
        input=markdown_input.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    fragment = proc.stdout.decode("utf-8")
    return re.sub(
        r'href="([0-9A-Za-z_.-]+\.md)"',
        r'href="#" data-chapter-file="\1"',
        fragment,
    )


def build_dist_docs_html(
    package_name: str,
    title: str | None = None,
    out_dir: Path = Path("."),
) -> tuple[Path, Path]:
    """Собирает MANUAL.html (по одному разделу за раз) и LICENSE.html через pandoc в UTF-8."""
    pages = load_package_docs(package_name)
    if not pages:
        raise FileNotFoundError(f"Не найдены .md главы в пакете '{package_name}'")

    resolved_title = title or _DEFAULT_TITLES.get(
        package_name, f"{package_name} — Руководство пользователя"
    )
    esc_title = html.escape(resolved_title)
    out_dir.mkdir(parents=True, exist_ok=True)

    nav_items: list[str] = []
    sections_html: list[str] = []
    total = len(pages)

    for idx, p in enumerate(pages):
        active_cls = " active" if idx == 0 else ""
        esc_ch_title = html.escape(p.title)
        nav_items.append(
            f'<a class="nav-link{active_cls}" onclick="showChapter({idx})">{esc_ch_title}</a>'
        )
        body_html = _run_pandoc_fragment(_strip_nav_block(p.markdown_text))
        prev_disabled = "disabled" if idx == 0 else ""
        next_disabled = "disabled" if idx == total - 1 else ""
        pager = (
            f'<div class="pager-row">'
            f'<button class="pager-btn" onclick="showChapter({idx - 1})" {prev_disabled}>← Предыдущий раздел</button>'
            f'<button class="pager-btn" onclick="showChapter({idx + 1})" {next_disabled}>Следующий раздел →</button>'
            f"</div>"
        )
        sections_html.append(
            f'<section class="chapter-page{active_cls}" id="ch-{idx}" '
            f'data-index="{idx}" data-filename="{html.escape(p.filename)}">\n'
            f"{body_html}\n{pager}\n</section>"
        )

    manual_doc = (
        '<!DOCTYPE html>\n<html lang="ru">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        f"<title>{esc_title}</title>\n"
        f"<style>{_MANUAL_STYLE_AND_SCRIPT}</style>\n"
        f"<script>{_MANUAL_JS}</script>\n"
        "</head>\n<body>\n"
        f'<aside class="sidebar"><h2>{esc_title}</h2>\n'
        + "\n".join(nav_items)
        + "\n</aside>\n"
        + '<main class="content">\n'
        + "\n".join(sections_html)
        + "\n</main>\n</body>\n</html>\n"
    )
    manual_path = out_dir / "MANUAL.html"
    manual_path.write_text(manual_doc, encoding="utf-8")

    assets = importlib.resources.files("fusion_common").joinpath("assets")
    ru_md = assets.joinpath("LICENSE.ru.md").read_text(encoding="utf-8")
    en_md = assets.joinpath("LICENSE.md").read_text(encoding="utf-8")
    lic_body = _run_pandoc_fragment(f"{ru_md}\n\n---\n\n{en_md}\n")
    lic_doc = (
        '<!DOCTYPE html>\n<html lang="ru">\n<head>\n'
        '<meta charset="utf-8" />\n'
        "<title>GNU General Public License v3.0</title>\n"
        f"<style>{_MANUAL_STYLE_AND_SCRIPT}</style>\n"
        "</head>\n<body>\n"
        f'<main class="content">{lic_body}</main>\n'
        "</body>\n</html>\n"
    )
    lic_path = out_dir / "LICENSE.html"
    lic_path.write_text(lic_doc, encoding="utf-8")
    return manual_path, lic_path


def _resolve_prebuilt_html(filename: str, package_name: str) -> Path | None:
    """Ищет готовый файл MANUAL.html / LICENSE.html рядом с .exe или в папке проекта."""
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
    """Открывает готовый файл MANUAL.html рядом с .exe в системном браузере."""
    _ = (app_title, icon_bg_color, initial_file)
    target = _resolve_prebuilt_html("MANUAL.html", package_name)
    if target is None:
        log.bind(package=package_name).warning("Prebuilt MANUAL.html not found")
        QtWidgets.QMessageBox.information(
            parent,
            "Руководство пользователя",
            "Собранный файл MANUAL.html не найден рядом с программой.\n"
            f"Для генерации выполните: uv run fusion-docs --package {package_name}",
        )
        return
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))


def show_fusion_license(
    app_title: str,
    icon_bg_color: str = ANALYSIS_ICON_BG,
    parent: QtWidgets.QWidget | None = None,
) -> None:
    """Открывает готовый файл LICENSE.html рядом с .exe в системном браузере."""
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
    parser.add_argument("--title", default=None)
    parser.add_argument("--out-dir", default=".")
    args = parser.parse_args()
    build_dist_docs_html(
        package_name=str(args.package),
        title=str(args.title) if args.title else None,
        out_dir=Path(args.out_dir),
    )


if __name__ == "__main__":
    main_docs_cli()
