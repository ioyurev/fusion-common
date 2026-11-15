from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from fusion_common.calibration import CalibrationSpec
from fusion_common.thermometry import CjcData, SensorSpec


class ChannelKey(StrEnum):
    """Канонические имена колонок контейнера .fus / .fusa."""

    TIME = "time"
    EMF_1 = "emf_1"
    EMF_2 = "emf_2"
    DTA = "dta"
    TEMPERATURE = "temperature"
    MASS = "mass"
    HEATER_OUTPUT = "heater_output"


class OnsetStateModel(BaseModel):
    ci: int
    bi: int
    is_onset: bool = True
    label_enabled: bool = True
    label_time_enabled: bool = True
    label_temp_enabled: bool = False
    label_dta_enabled: bool = False


class PeakStateModel(BaseModel):
    t1: float
    t2: float
    baseline_type: int = 0
    label_enabled: bool = True
    onsets: list[OnsetStateModel] = Field(default_factory=list)


class CurveStateModel(BaseModel):
    color: str = "#0284c7"
    dy: float = 0.0
    visible: bool = True
    peaks: list[PeakStateModel] = Field(default_factory=list)


class SegmentStateModel(BaseModel):
    color: str = "#0284c7"
    dy: float = 0.0
    peaks: list[PeakStateModel] = Field(default_factory=list)
    curves: dict[str, CurveStateModel] = Field(default_factory=dict)


class DerivativeSpecModel(BaseModel):
    y_col: str
    x_col: str
    order: int = 1


class AnalysisStateModel(BaseModel):
    """Типизированный контракт состояния проекта анализа .fusa."""

    deriv_channel: str = "DTA"
    split_times: list[float] = Field(default_factory=list)
    smoothing_windows: dict[str, int] = Field(default_factory=dict)
    computed_derivatives: list[DerivativeSpecModel] = Field(default_factory=list)
    segments: list[SegmentStateModel] = Field(default_factory=list)


class ChannelDescriptor(BaseModel):
    """Самодостаточное описание одного записанного или вычисленного канала данных."""

    column: str
    label: str
    unit: str
    source_columns: list[str] | None = None
    sensor: SensorSpec | None = None
    cjc: CjcData | None = None
    calibration: CalibrationSpec | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MeasurementManifest(BaseModel):
    """Манифест контейнера измерения (.fus) и проекта анализа (.fusa) с паспортом SCRAM/ICTAC."""

    schema_version: str = "1.0"
    software_version: str = ""
    common_version: str = ""
    sample: str = ""
    operator: str = ""
    instrument: str = ""
    sample_mass_mg: float | None = None
    crucible: str = ""
    atmosphere: str = ""
    reference_material: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    comment: str = ""
    channels: list[ChannelDescriptor] = Field(default_factory=list)
    analysis_state: dict[str, Any] = Field(default_factory=dict)


@dataclass
class MeasurementRecord:
    """Унифицированное представление измерения в памяти."""

    manifest: MeasurementManifest
    data: dict[str, np.ndarray] = field(default_factory=dict)
    source_path: Path | None = None
    source_format: str = "FUS"
    source_version: str = "1.0"

    @property
    def time_array(self) -> np.ndarray:
        for key in ("time", "Time/s", "time, s", "Time"):
            if key in self.data:
                return self.data[key]
        for ch in self.manifest.channels:
            if ch.unit in ("s", "sec") and ch.column in self.data:
                return self.data[ch.column]
        raise KeyError("В записи измерения отсутствует канал времени (time)")

    def get_channel(self, column: str) -> np.ndarray | None:
        return self.data.get(column)

    def get_descriptor(self, column: str) -> ChannelDescriptor | None:
        for ch in self.manifest.channels:
            if ch.column == column:
                return ch
        return None

    def ordered_columns(self) -> list[str]:
        cols = [ch.column for ch in self.manifest.channels if ch.column in self.data]
        for col in self.data:
            if col not in cols:
                cols.append(col)
        return cols

    def set_smoothed_channel(
        self,
        source_column: str,
        smoothed_values: np.ndarray,
        smoothing_window: int,
        target_column: str | None = None,
        label: str | None = None,
    ) -> None:
        """Неразрушающе добавляет или обновляет сглаженный канал внутри этой же записи."""
        if target_column is None:
            target_column = f"{source_column}__smoothed"
        src_desc = self.get_descriptor(source_column)
        base_label = src_desc.label if src_desc is not None else source_column
        if label is None:
            label = f"{base_label} (сглаж.)"
        unit = src_desc.unit if src_desc is not None else "mV"
        exo_up = src_desc.metadata.get("exo_up", True) if src_desc is not None else True
        meta: dict[str, Any] = {
            "exo_up": exo_up,
            "smoothing_window": int(smoothing_window),
        }
        new_desc = ChannelDescriptor(
            column=target_column,
            label=label,
            unit=unit,
            source_columns=[source_column],
            metadata=meta,
        )
        replaced = False
        for idx, ch in enumerate(self.manifest.channels):
            if ch.column == target_column:
                self.manifest.channels[idx] = new_desc
                replaced = True
                break
        if not replaced:
            self.manifest.channels.append(new_desc)
        self.data[target_column] = np.asarray(smoothed_values, dtype=float)
