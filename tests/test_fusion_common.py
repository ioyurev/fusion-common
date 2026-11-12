from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pytest
from fusion_common import (
    CURRENT_SCHEMA_VERSION,
    AdditivePolynomialCalibration,
    ChannelDescriptor,
    CjcData,
    IdentityCalibration,
    LookupSplineCalibration,
    LookupSplineSensorModel,
    MeasurementManifest,
    MeasurementRecord,
    PiecewisePolynomialSegment,
    PiecewisePolynomialSensorModel,
    PolynomialSensorModel,
    Standard,
    ThermocoupleManager,
    centered_moving_average,
    evaluate_channel_temperature,
    load_measurement,
    make_vr_thermocouple,
    make_xa_thermocouple,
    migrate_manifest_dict,
    save_fus,
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


def test_thermocouple_manager_crud_and_gost_reference_points(tmp_path: Path) -> None:
    from fusion_common import make_gost_thermocouples

    mgr = ThermocoupleManager(tmp_path / "tc")
    assert len(mgr.list_names()) == 12
    assert len(make_gost_thermocouples()) == 12

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

    custom = PolynomialSensorModel(
        name="Custom_Type_S",
        coefficients=[0.0, 100.0, -0.5],
    )
    mgr.save_sensor(custom)
    mgr.load_all()
    loaded = mgr.load_sensor("Custom_Type_S")
    assert isinstance(loaded, PolynomialSensorModel)
    assert loaded.coefficients == [0.0, 100.0, -0.5]

    mgr.delete_sensor("Custom_Type_S")
    assert "Custom_Type_S" not in mgr.list_names()


def test_calibration_models_and_statistics() -> None:
    from fusion_common import REFERENCE_STANDARDS

    assert REFERENCE_STANDARDS["In (Индий, плавление)"] == pytest.approx(156.61)
    assert REFERENCE_STANDARDS["Au (Золото, плавление)"] == pytest.approx(1064.18)
    assert "SiO2 (Кварц α→β)" not in REFERENCE_STANDARDS
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
    with pytest.raises(ValueError, match="Неподдерживаемая версия схемы"):
        migrate_manifest_dict({"schema_version": "99.0"})


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
    loaded = load_measurement(fus_path)

    assert loaded.manifest.schema_version == "1.0"
    assert loaded.manifest.sample == "Sm2S3-Gd2S3"
    assert len(loaded.manifest.channels) == 6
    dta_desc = loaded.get_descriptor("dta_1_2")
    assert dta_desc is not None
    assert dta_desc.metadata.get("exo_up") is True

    # Проверяем неразрушающее сохранение сглаженных данных в том же самом файле .fus
    smoothed = centered_moving_average(dta_1_2, window=3)
    loaded.set_smoothed_channel(
        source_column="dta_1_2",
        smoothed_values=smoothed,
        smoothing_window=3,
        target_column="dta_smoothed",
    )
    save_fus(loaded, fus_path)

    reloaded = load_measurement(fus_path)
    raw_ch = reloaded.get_channel("dta_1_2")
    sm_ch = reloaded.get_channel("dta_smoothed")
    sm_desc = reloaded.get_descriptor("dta_smoothed")
    assert raw_ch is not None and np.allclose(raw_ch, dta_1_2)
    assert sm_ch is not None and np.allclose(sm_ch, smoothed)
    assert sm_desc is not None and sm_desc.metadata.get("smoothing_window") == 3


def test_vtaz1_read_backward_compatibility(tmp_path: Path) -> None:
    tc = make_vr_thermocouple()
    cjc = CjcData(temperature=25.0, e_cold=0.31)
    cal = AdditivePolynomialCalibration(
        name="VTA_Cal",
        calibration_type="linear",
        coefficients=[0.01, -1.5, 0.0],
    )
    _t_arr = np.array([0.0, 1.0, 2.0])
    emf_arr = np.array([1.0, 2.0, 3.0])
    expected_temp = np.asarray(
        evaluate_channel_temperature(emf_arr, tc, cjc, cal), dtype=float
    )

    vtaz_path = tmp_path / "legacy.vtaz"
    with ZipFile(vtaz_path, "w", ZIP_DEFLATED) as zipf:
        zipf.writestr(
            "metadata.json",
            json.dumps({"sample": "Pt", "operator": "Tester", "vtaz_version": "1.0"}),
        )
        zipf.writestr("data_input.csv", "time, s,EMF, mV;\n0.0,1.0;\n1.0,2.0;\n2.0,3.0;\n")
        zipf.writestr("calibration.json", cal.model_dump_json())
        zipf.writestr(
            "thermocouple.json",
            json.dumps({"thermocouple_coefficients": tc.coefficients}),
        )
        zipf.writestr("cjc.json", cjc.model_dump_json())

    loaded = load_measurement(vtaz_path)
    assert loaded.source_format == "VTAZ"
    assert loaded.manifest.sample == "Pt"
    loaded_temp = loaded.get_channel("temperature")
    assert loaded_temp is not None
    assert np.allclose(loaded_temp, expected_temp, atol=1e-4)


def test_load_real_legacy_fus_mdt_if_present() -> None:
    legacy_file = (
        Path(__file__).resolve().parents[2]  # noqa: MI001
        / "fusion-collection"
        / "test.fus-mdt"
    )
    if not legacy_file.exists():
        pytest.skip("test.fus-mdt not present")
    record = load_measurement(legacy_file)
    assert record.source_format == "FUS-MDT"
    assert len(record.time_array) > 0


def test_splash_rendering_qimage(tmp_path: Path) -> None:
    import sys

    from fusion_common import generate_splash_png, get_pyproject_version
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(sys.argv)
    repo_root = Path(__file__).resolve().parents[1]  # noqa: MI001
    ver = get_pyproject_version(repo_root)
    assert ver.startswith("v0.")

    out_png = tmp_path / "splash.png"
    generate_splash_png(
        svg_path=tmp_path / "nonexistent.svg",
        output_path=out_png,
        version_str=ver,
        subtitle="Unit Test Subtitle",
    )
    assert out_png.exists() and out_png.stat().st_size > 1000


def test_calibration_manager_and_packaging_helpers(tmp_path: Path) -> None:
    from fusion_common import (
        CalibrationManager,
        get_appdata_path,
        write_pyinstaller_version_file,
    )

    cal_mgr = CalibrationManager(tmp_path / "cals")
    cal = AdditivePolynomialCalibration.from_standards(
        name="TestCal",
        standards=[
            Standard(name="In", t_theor=156.6, t_exp=155.0),
            Standard(name="Sn", t_theor=231.9, t_exp=230.0),
        ],
    )
    cal_mgr.save_calibration(cal)
    assert "TestCal" in cal_mgr.list_names()
    loaded_cal = cal_mgr.load_calibration("TestCal")
    assert loaded_cal.name == "TestCal"
    cal_mgr.delete_calibration("TestCal")
    assert "TestCal" not in cal_mgr.list_names()

    v_file = write_pyinstaller_version_file(
        package_name="fusion-test",
        product_name="Fusion Test",
        file_description="Test Desc",
        version_str="0.4.0",
        output_path=tmp_path / "version_text_file",
    )
    assert v_file.exists()
    assert get_appdata_path("fusion-test").exists()
