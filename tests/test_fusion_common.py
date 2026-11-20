from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pytest
from pydantic import ValidationError

from fusion_common import (
    CURRENT_SCHEMA_VERSION,
    AdditivePolynomialCalibration,
    ChannelDescriptor,
    CjcData,
    IdentityCalibration,
    IncompatibleFileVersionError,
    LookupSplineCalibration,
    LookupSplineSensorModel,
    MeasurementManifest,
    MeasurementRecord,
    PiecewisePolynomialSegment,
    PiecewisePolynomialSensorModel,
    Standard,
    allocate_log_file,
    evaluate_channel_temperature,
    is_newer_version,
    load_fus,
    make_vr_thermocouple,
    make_xa_thermocouple,
    migrate_manifest_dict,
    save_fus,
    validate_manifest_dict,
)


def test_thermocouple_roundtrip_vr_and_xa() -> None:
    for tc in (make_vr_thermocouple(), make_xa_thermocouple()):
        emf_in = 5.0
        temp_c = float(tc.emf_to_temperature(emf_in))
        emf_out = tc.temperature_to_emf(temp_c)
        assert emf_out == pytest.approx(emf_in, abs=1e-3)


def test_piecewise_and_lookup_sensors() -> None:
    pw = PiecewisePolynomialSensorModel(
        name="PiecewiseTC",
        segments=[
            PiecewisePolynomialSegment(
                emf_min_mv=-5.0, emf_max_mv=10.0, coefficients=[0.0, 25.0]
            ),
            PiecewisePolynomialSegment(
                emf_min_mv=10.0, emf_max_mv=50.0, coefficients=[50.0, 20.0]
            ),
        ],
    )
    assert float(pw.emf_to_temperature(4.0)) == pytest.approx(100.0)
    assert float(pw.emf_to_temperature(20.0)) == pytest.approx(450.0)

    lut = LookupSplineSensorModel(
        name="TableTC",
        emf_points_mv=[0.0, 10.0, 20.0],
        temp_points_c=[0.0, 250.0, 500.0],
    )
    assert float(lut.emf_to_temperature(5.0)) == pytest.approx(125.0)
    assert lut.temperature_to_emf(375.0) == pytest.approx(15.0)


def test_gost_thermocouples_reference_points() -> None:
    from fusion_common import make_gost_thermocouples

    assert len(make_gost_thermocouples()) == 12
    assert make_xa_thermocouple().display_short_name == "ТХА (тип K)"
    assert make_vr_thermocouple().display_short_name == "ТВР (тип А-1)"

    # Контрольные точки ТХА (тип K) во всех трёх поддиапазонах ГОСТ Р 8.585-2001:
    # -100 °C -> -3.554 мВ; 200 °C -> 8.138 мВ; 500 °C -> 20.644 мВ;
    # 1000 °C -> 41.276 мВ; 1300 °C -> 52.410 мВ
    xa = make_xa_thermocouple()
    assert float(xa.emf_to_temperature(-3.554)) == pytest.approx(-100.0, abs=0.1)
    assert float(xa.emf_to_temperature(8.138)) == pytest.approx(200.0, abs=0.1)
    assert float(xa.emf_to_temperature(20.644)) == pytest.approx(500.0, abs=0.1)
    assert float(xa.emf_to_temperature(41.276)) == pytest.approx(1000.0, abs=0.1)
    assert float(xa.emf_to_temperature(52.410)) == pytest.approx(1300.0, abs=0.1)

    # Контрольные точки ТВР (тип А-1) по ГОСТ Р 8.585-2001:
    # 1000 °C -> 16.129 мВ; 2000 °C -> 29.186 мВ; 2500 °C -> 33.640 мВ
    vr = make_vr_thermocouple()
    assert float(vr.emf_to_temperature(16.129)) == pytest.approx(1000.0, abs=0.5)
    assert float(vr.emf_to_temperature(29.186)) == pytest.approx(2000.0, abs=0.6)
    assert float(vr.emf_to_temperature(33.640)) == pytest.approx(2500.0, abs=1.0)


def test_calibration_models_and_statistics() -> None:
    standards = [
        Standard(name="In", t_theor=156.6, t_exp=155.6),
        Standard(name="Sn", t_theor=231.9, t_exp=229.9),
        Standard(name="Zn", t_theor=419.5, t_exp=416.5),
    ]
    cal = AdditivePolynomialCalibration.from_standards(
        name="DTA_Lin",
        standards=standards,
        calibration_type="linear",
    )
    stats = cal.calculate_statistics()
    assert stats.n_points == 3
    assert stats.sec is not None
    assert stats.expanded_uncertainty == pytest.approx(2.0 * stats.sec)

    pw_cal = AdditivePolynomialCalibration.from_standards(
        name="DTA_PW",
        standards=standards,
        calibration_type="piecewise",
    )
    assert float(pw_cal.apply(155.6)) == pytest.approx(156.6)
    assert pw_cal.valid_temp_range == pytest.approx((155.6, 416.5))

    off_cal = AdditivePolynomialCalibration.from_standards(
        name="OnePoint",
        standards=[Standard(name="In", t_theor=156.6, t_exp=155.6)],
        calibration_type="offset",
    )
    assert float(off_cal.apply(200.0)) == pytest.approx(201.0)

    zero = IdentityCalibration()
    assert float(zero.apply(500.0)) == 500.0

    lut_cal = LookupSplineCalibration(
        name="SplineCal",
        t_exp_points=[100.0, 200.0, 300.0],
        t_theor_points=[102.0, 205.0, 310.0],
    )
    assert float(lut_cal.apply(150.0)) == pytest.approx(153.5)


def test_schema_migration_boundary() -> None:
    migrated = migrate_manifest_dict({"schema_version": "0.9", "sample": "Test"})
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION

    with pytest.raises(
        IncompatibleFileVersionError,
        match="Обновите программу",
    ):
        migrate_manifest_dict(
            {
                "schema_version": "2.0",
                "software_version": "1.0.0",
                "common_version": "2.0.0",
            }
        )

    with pytest.raises(
        IncompatibleFileVersionError,
        match="Неподдерживаемая версия схемы",
    ):
        migrate_manifest_dict({"schema_version": "99.0"})

    with pytest.raises(ValueError, match="Неподдерживаемая версия схемы") as exc_info:
        migrate_manifest_dict({"schema_version": "0.1"})
    assert not isinstance(exc_info.value, IncompatibleFileVersionError)


def test_validate_manifest_forward_compat_and_newer_kind() -> None:
    assert is_newer_version("1.1", "1.0") is True
    assert is_newer_version("0.7.3", "0.7.3") is False
    assert is_newer_version("0.7.2", "0.7.3") is False

    manifest = validate_manifest_dict(
        {
            "schema_version": "1.0",
            "common_version": "99.0.0",
            "sample": "ForwardSample",
            "future_extra_field": "ignored-by-old-client",
        },
        current_common_version="0.7.3",
    )
    assert manifest.sample == "ForwardSample"

    future_kind_payload = {
        "schema_version": "1.0",
        "software_version": "2.1.0",
        "common_version": "99.0.0",
        "channels": [
            {
                "column": "temperature",
                "label": "T",
                "unit": "°C",
                "sensor": {"kind": "future_rtd_pt100", "name": "Pt100"},
            }
        ],
    }
    with pytest.raises(
        IncompatibleFileVersionError,
        match="Обновите программу",
    ):
        validate_manifest_dict(future_kind_payload, current_common_version="0.7.3")

    corrupted_old_payload = dict(future_kind_payload, common_version="0.5.0")
    with pytest.raises(ValidationError):
        validate_manifest_dict(corrupted_old_payload, current_common_version="0.7.3")


def test_load_fus_corrupted_zip_and_missing_members(tmp_path: Path) -> None:
    stub = tmp_path / "stub.fus"
    stub.write_bytes(b"\x00")
    with pytest.raises(ValueError, match="не была завершена"):
        load_fus(stub)

    no_manifest = tmp_path / "no_manifest.fus"
    with ZipFile(no_manifest, "w", ZIP_DEFLATED) as zf:
        zf.writestr("data.csv", "time\n0.0\n")
    with pytest.raises(ValueError, match=r"отсутствует manifest\.json"):
        load_fus(no_manifest)

    no_data = tmp_path / "no_data.fus"
    with ZipFile(no_data, "w", ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", MeasurementManifest(sample="S").model_dump_json())
    with pytest.raises(ValueError, match=r"отсутствует data\.csv"):
        load_fus(no_data)

    fusa_path = tmp_path / "project.fusa"
    save_fus(
        MeasurementRecord(
            manifest=MeasurementManifest(
                sample="S",
                channels=[ChannelDescriptor(column="time", label="Время", unit="s")],
            ),
            data={"time": np.array([0.0, 1.0])},
        ),
        fusa_path,
    )
    assert load_fus(fusa_path).source_format == "FUSA"


def test_multi_thermocouple_fus_roundtrip(tmp_path: Path) -> None:
    tc_vr = make_vr_thermocouple()
    cjc_1 = CjcData.from_sensor(24.0, tc_vr)
    cal_1 = AdditivePolynomialCalibration.from_standards(
        name="Cal1",
        standards=[
            Standard(name="Au", t_theor=1064.2, t_exp=1060.0),
            Standard(name="Pd", t_theor=1554.9, t_exp=1548.0),
        ],
    )

    t_arr = np.linspace(0.0, 10.0, 11)
    emf_1 = np.linspace(1.0, 15.0, 11)
    emf_2 = np.linspace(1.0, 14.8, 11)
    emf_3 = np.linspace(0.5, 12.0, 11)
    temp_1 = np.asarray(
        evaluate_channel_temperature(emf_1, tc_vr, cjc_1, cal_1), dtype=float
    )
    dta_1_2 = emf_1 - emf_2

    manifest = MeasurementManifest(
        sample="Sm2S3-Gd2S3",
        operator="Yurev",
        comment="Three-thermocouple test",
        channels=[
            ChannelDescriptor(column="time", label="Время", unit="s"),
            ChannelDescriptor(column="emf_1", label="ЭДС образца 1", unit="mV"),
            ChannelDescriptor(column="emf_2", label="ЭДС эталона", unit="mV"),
            ChannelDescriptor(column="emf_3", label="ЭДС печи", unit="mV"),
            ChannelDescriptor(
                column="temp_1",
                label="Температура образца 1",
                unit="°C",
                source_columns=["emf_1"],
                sensor=tc_vr,
                cjc=cjc_1,
                calibration=cal_1,
            ),
            ChannelDescriptor(
                column="dta_1_2",
                label="ДТА (1-2)",
                unit="mV",
                source_columns=["emf_1", "emf_2"],
                metadata={"exo_up": True},
            ),
        ],
    )
    record = MeasurementRecord(
        manifest=manifest,
        data={
            "time": t_arr,
            "emf_1": emf_1,
            "emf_2": emf_2,
            "emf_3": emf_3,
            "temp_1": temp_1,
            "dta_1_2": dta_1_2,
        },
    )

    fus_path = tmp_path / "multi_tc.fus"
    save_fus(record, fus_path)
    loaded = load_fus(fus_path)

    assert loaded.manifest.schema_version == "1.0"
    assert loaded.manifest.sample == "Sm2S3-Gd2S3"
    assert len(loaded.manifest.channels) == 6
    dta_desc = loaded.get_descriptor("dta_1_2")
    assert dta_desc is not None
    assert dta_desc.metadata.get("exo_up") is True

    # Проверяем неразрушающее сохранение сглаженных данных в том же самом файле .fus
    smoothed = dta_1_2 * 0.5
    loaded.set_smoothed_channel(
        source_column="dta_1_2",
        smoothed_values=smoothed,
        smoothing_window=3,
    )
    save_fus(loaded, fus_path)

    reloaded = load_fus(fus_path)
    raw_ch = reloaded.get_channel("dta_1_2")
    sm_ch = reloaded.get_channel("dta_1_2__smoothed")
    sm_desc = reloaded.get_descriptor("dta_1_2__smoothed")
    assert raw_ch is not None
    assert np.allclose(raw_ch, dta_1_2)
    assert sm_ch is not None
    assert np.allclose(sm_ch, smoothed)
    assert sm_desc is not None
    assert sm_desc.metadata.get("smoothing_window") == 3


def test_icon_svg_rendering() -> None:
    from fusion_common import COMMON_ICON_BG, generate_app_icon_svg

    svg_icon = Path(__file__).resolve().parent.parent / "assets" / "icon.svg"
    generate_app_icon_svg(COMMON_ICON_BG, svg_icon)
    assert svg_icon.exists()
    assert "<rect" in svg_icon.read_text(encoding="utf-8")


def test_splash_rendering_qimage(tmp_path: Path) -> None:
    import sys

    from PySide6.QtWidgets import QApplication

    from fusion_common import generate_splash_png, get_pyproject_version

    _app = QApplication.instance() or QApplication(sys.argv)
    repo_root = Path(__file__).resolve().parents[1]  # noqa: MI001
    ver = get_pyproject_version(repo_root)
    assert ver.startswith("v0.")

    out_png = tmp_path / "splash.png"
    icon_png = tmp_path / "icon.png"
    generate_splash_png(
        svg_path=None,
        output_path=out_png,
        version_str=ver,
        subtitle="Unit Test Subtitle",
        icon_bg_color="#FD700D",
        icon_output_path=icon_png,
    )
    assert out_png.exists()
    assert out_png.stat().st_size > 1000
    assert icon_png.exists()
    assert icon_png.stat().st_size > 1000
    assert (tmp_path / "wizard_large.bmp").exists()
    assert (tmp_path / "wizard_small.bmp").exists()

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QStyle

    from fusion_common import resolve_ui_icon

    ico = resolve_ui_icon(
        QIcon.ThemeIcon.DocumentOpen,
        QStyle.StandardPixmap.SP_DialogOpenButton,
    )
    assert not ico.isNull()


def test_packaging_helpers(tmp_path: Path) -> None:
    from fusion_common import (
        get_appdata_path,
        write_pyinstaller_version_file,
    )

    v_file = write_pyinstaller_version_file(
        package_name="fusion-test",
        product_name="Fusion Test",
        file_description="Test Desc",
        version_str="0.4.0",
        output_path=tmp_path / "version_text_file",
    )
    assert v_file.exists()
    assert get_appdata_path("fusion-test").exists()

    from fusion_common.docs_viewer import _strip_nav_block

    sample_md = (
        "# 1. Заголовок\n\nВведение.\n\n"
        "Навигация по руководству:\n- [2. Глава](02.md)\n\n"
        + ("-" * 80)
        + "\n\n## 1.1. Подраздел\n\nОсновной текст подраздела."
    )
    stripped = _strip_nav_block(sample_md)
    assert "Навигация по руководству:" not in stripped
    assert "## 1.1. Подраздел" in stripped
    assert "Основной текст подраздела." in stripped


def test_sequential_log_allocation_and_retention(tmp_path: Path) -> None:
    from fusion_common.splash import LOG_SEQ_FILENAME

    log_dir = tmp_path / "logs"
    reset_clock = datetime(2025, 9, 12, 5, 36, 42, tzinfo=UTC)
    for expected_idx in range(1, 5):
        path = allocate_log_file(log_dir, max_retained=3, now=reset_clock)
        assert path.name == f"{expected_idx:05d}_2025-09-12_05-36-42.log"
        path.write_text("entry\n", encoding="utf-8")

    remaining = sorted(p.name for p in log_dir.glob("*.log"))
    assert len(remaining) == 3
    first_log, *_, last_log = remaining
    assert first_log.startswith("00002_")
    assert last_log.startswith("00004_")

    # Очистка папки логов не сбрасывает счётчик при наличии .log_seq
    for p in log_dir.glob("*.log"):
        p.unlink()
    p5 = allocate_log_file(log_dir, max_retained=3, now=reset_clock)
    assert p5.name.startswith("00005_")
    p5.write_text("entry\n", encoding="utf-8")

    # Удаление .log_seq восстанавливает счётчик по максимальному номеру оставшегося файла
    (log_dir / LOG_SEQ_FILENAME).unlink()
    p6 = allocate_log_file(log_dir, max_retained=3, now=reset_clock)
    assert p6.name.startswith("00006_")
