from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger as log

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


def _find_iscc_executable() -> Path | None:
    from_path = shutil.which("ISCC") or shutil.which("iscc")
    if from_path:
        return Path(from_path)
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
        / "Inno Setup 6"
        / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        / "Inno Setup 6"
        / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "Inno Setup 6"
        / "ISCC.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def build_inno_setup_installer(
    package_name: str,
    product_name: str,
    version_str: str,
    dist_root: Path = Path("dist"),
    icon_path: Path = Path("icon.ico"),
) -> Path:
    """Генерирует скрипт Inno Setup (.iss) с поддержкой Admin/User и ярлыками и собирает Setup.exe."""
    clean_ver = version_str.lstrip("v")
    source_dir = (dist_root / package_name).resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Папка сборки не найдена: {source_dir}")

    out_dir = dist_root.resolve()
    setup_base = f"{package_name}_setup_v{clean_ver}"
    resolved_icon = icon_path.resolve()
    setup_icon_line = (
        f"SetupIconFile={resolved_icon}\n" if resolved_icon.is_file() else ""
    )
    wiz_large = (icon_path.parent / "wizard_large.bmp").resolve()
    wiz_small = (icon_path.parent / "wizard_small.bmp").resolve()
    wiz_large_line = f"WizardImageFile={wiz_large}\n" if wiz_large.is_file() else ""
    wiz_small_line = (
        f"WizardSmallImageFile={wiz_small}\n" if wiz_small.is_file() else ""
    )

    iss_content = (
        f"[Setup]\n"
        f"AppId={{{{{package_name}-yurev-thermal-suite}}\n"
        f"AppName={product_name}\n"
        f"AppVersion={clean_ver}\n"
        f"AppPublisher=Юрьев Илья Олегович\n"
        f"DefaultDirName={{autopf}}\\{product_name}\n"
        f"DefaultGroupName={product_name}\n"
        f"PrivilegesRequired=lowest\n"
        f"PrivilegesRequiredOverridesAllowed=dialog commandline\n"
        f"OutputDir={out_dir}\n"
        f"OutputBaseFilename={setup_base}\n"
        f"{setup_icon_line}"
        f"{wiz_large_line}"
        f"{wiz_small_line}"
        f"UninstallDisplayIcon={{app}}\\{package_name}.exe\n"
        f"Compression=lzma2/ultra64\n"
        f"SolidCompression=yes\n"
        f"WizardStyle=modern\n"
        f"ArchitecturesAllowed=x64compatible\n"
        f"ArchitecturesInstallIn64BitMode=x64compatible\n\n"
        f"[Languages]\n"
        f'Name: "russian"; MessagesFile: "compiler:Languages\\Russian.isl"\n\n'
        f"[Tasks]\n"
        f'Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; '
        f'GroupDescription: "{{cm:AdditionalIcons}}"\n\n'
        f"[Files]\n"
        f'Source: "{source_dir}\\*"; DestDir: "{{app}}"; '
        f"Flags: ignoreversion recursesubdirs createallsubdirs\n\n"
        f"[Icons]\n"
        f'Name: "{{group}}\\{product_name}"; Filename: "{{app}}\\{package_name}.exe"\n'
        f'Name: "{{group}}\\Руководство пользователя ({product_name})"; '
        f'Filename: "{{app}}\\MANUAL.html"\n'
        f'Name: "{{group}}\\Лицензия GNU GPL v3.0"; '
        f'Filename: "{{app}}\\LICENSE.html"\n'
        f'Name: "{{group}}\\Удалить {product_name}"; Filename: "{{uninstallexe}}"\n'
        f'Name: "{{autodesktop}}\\{product_name}"; '
        f'Filename: "{{app}}\\{package_name}.exe"; Tasks: desktopicon\n\n'
        f"[Run]\n"
        f'Filename: "{{app}}\\{package_name}.exe"; '
        f'Description: "Запустить {product_name}"; '
        f"Flags: nowait postinstall skipifsilent\n"
    )
    iss_path = dist_root.parent / f"{package_name}.iss"
    iss_path.write_text(iss_content, encoding="utf-8-sig")

    iscc = _find_iscc_executable()
    if iscc is None:
        log.bind(iss=str(iss_path)).warning(
            "ISCC.exe (Inno Setup 6) not found; .iss script generated, skipping compilation"
        )
        return iss_path

    subprocess.run([str(iscc), str(iss_path)], check=True)
    return out_dir / f"{setup_base}.exe"


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

    p_inst = sub.add_parser("installer")
    p_inst.add_argument("--package", required=True)
    p_inst.add_argument("--product", required=True)
    p_inst.add_argument("--version-from", required=True)
    p_inst.add_argument("--dist-root", default="dist")
    p_inst.add_argument("--icon", default="icon.ico")

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
    elif args.cmd == "installer":
        build_inno_setup_installer(
            package_name=args.package,
            product_name=args.product,
            version_str=ver,
            dist_root=Path(args.dist_root),
            icon_path=Path(args.icon),
        )


if __name__ == "__main__":
    main_packaging_cli()
