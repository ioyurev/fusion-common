from importlib.metadata import PackageNotFoundError, version

from fusion_common.calibration import (
    AdditivePolynomialCalibration,
    CalibrationSpec,
    CalibrationStats,
    DirectPolynomialCalibration,
    IdentityCalibration,
    LookupSplineCalibration,
    Standard,
    calculate_coefficients,
    evaluate_channel_temperature,
)
from fusion_common.formats import (
    ALL_SUPPORTED_FILTER,
    FUS_EXT_FILTER,
    load_fus,
    load_fus_fmdt,
    load_fus_mdt,
    load_measurement,
    load_tda,
    load_vtaz,
    save_fus,
)
from fusion_common.math_utils import (
    bisection_method,
    calculate_speed,
    eval_polynomial_asc,
)
from fusion_common.migration import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    migrate_manifest_dict,
)
from fusion_common.models import (
    ChannelDescriptor,
    MeasurementManifest,
    MeasurementRecord,
)
from fusion_common.thermometry import (
    DEFAULT_VR_COEFFICIENTS,
    DEFAULT_XA_COEFFICIENTS,
    CjcData,
    LookupSplineSensorModel,
    PiecewisePolynomialSegment,
    PiecewisePolynomialSensorModel,
    PolynomialSensorModel,
    SensorSpec,
    ThermocoupleManager,
    make_vr_thermocouple,
    make_xa_thermocouple,
)

try:
    __version__ = version("fusion-common")
except PackageNotFoundError:
    __version__ = "0.2.0"

__all__ = [
    "ALL_SUPPORTED_FILTER",
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_VR_COEFFICIENTS",
    "DEFAULT_XA_COEFFICIENTS",
    "FUS_EXT_FILTER",
    "MIGRATIONS",
    "AdditivePolynomialCalibration",
    "CalibrationSpec",
    "CalibrationStats",
    "ChannelDescriptor",
    "CjcData",
    "DirectPolynomialCalibration",
    "IdentityCalibration",
    "LookupSplineCalibration",
    "LookupSplineSensorModel",
    "MeasurementManifest",
    "MeasurementRecord",
    "PiecewisePolynomialSegment",
    "PiecewisePolynomialSensorModel",
    "PolynomialSensorModel",
    "SensorSpec",
    "Standard",
    "ThermocoupleManager",
    "__version__",
    "bisection_method",
    "calculate_coefficients",
    "calculate_speed",
    "eval_polynomial_asc",
    "evaluate_channel_temperature",
    "load_fus",
    "load_fus_fmdt",
    "load_fus_mdt",
    "load_measurement",
    "load_tda",
    "load_vtaz",
    "make_vr_thermocouple",
    "make_xa_thermocouple",
    "migrate_manifest_dict",
    "save_fus",
]
