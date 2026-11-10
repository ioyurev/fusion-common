from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from fusion_common.splash import get_pyproject_version


def get_appdata_path(app_name: str) -> Path:
    """Возвращает каталог данных приложения в профиле пользователя (единый SSOT)."""
    if sys.platform.startswith("linux") or sys.platform.startswith("darwin"):
        base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    else:
        base = os.getenv("APPDATA") or os.path.expanduser("~")
    path = Path(base) / app_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_pyinstaller_version_file(
    package_name: str,
    product_name: str,
    file_description: str,
    version_str: str,
    output_path: Path = Path("version_text_file"),
) -> Path:
    """Генерирует файл ресурса версии Windows для pyi-set_version."""
    clean_ver = version_str.lstrip("v")
    parts = (clean_ver.split(".") + ["0", "0", "0"])[:4]
    ver_tuple = ", ".join(str(int(p)) if p.isdigit() else "0" for p in parts)

    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({ver_tuple}),
    prodvers=({ver_tuple}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Ilya O. Yurev'),
        StringStruct('FileDescription', '{file_description}'),
        StringStruct('FileVersion', '{clean_ver}'),
        StringStruct('InternalName', '{package_name}'),
        StringStruct('LegalCopyright', 'GPL-3.0-or-later'),
        StringStruct('OriginalFilename', '{package_name}.exe'),
        StringStruct('ProductName', '{product_name}'),
        StringStruct('ProductVersion', '{clean_ver}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    output_path.write_text(content, encoding="utf-8")
    return output_path


def make_dist_zip(
    package_name: str,
    version_str: str,
    dist_root: Path = Path("dist"),
) -> Path:
    """Упаковывает собранную папку dist/<package_name> в ZIP-архив релиза."""
    clean_ver = version_str.lstrip("v")
    archive_base = dist_root / f"{package_name}_v{clean_ver}"
    source_dir = dist_root / package_name
    created = shutil.make_archive(str(archive_base), "zip", str(source_dir))
    return Path(created)


def main_packaging_cli() -> None:
    parser = argparse.ArgumentParser(description="Fusion Build Packaging Utilities")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ver = sub.add_parser("version-file")
    p_ver.add_argument("--package", required=True)
    p_ver.add_argument("--product", required=True)
    p_ver.add_argument("--description", required=True)
    p_ver.add_argument("--version-from", required=True)
    p_ver.add_argument("--out", default="version_text_file")

    p_zip = sub.add_parser("zip-dist")
    p_zip.add_argument("--package", required=True)
    p_zip.add_argument("--version-from", required=True)
    p_zip.add_argument("--dist-root", default="dist")

    args = parser.parse_args()
    ver = get_pyproject_version(Path(args.version_from)).lstrip("v")
    if args.cmd == "version-file":
        write_pyinstaller_version_file(
            package_name=args.package,
            product_name=args.product,
            file_description=args.description,
            version_str=ver,
            output_path=Path(args.out),
        )
    elif args.cmd == "zip-dist":
        make_dist_zip(
            package_name=args.package,
            version_str=ver,
            dist_root=Path(args.dist_root),
        )


if __name__ == "__main__":
    main_packaging_cli()
