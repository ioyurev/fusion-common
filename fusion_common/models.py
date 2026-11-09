from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from fusion_common.calibration import CalibrationSpec
from fusion_common.thermometry import CjcData, SensorSpec


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
    """Манифест единого контейнера измерения .fus без привязки к фиксированной модальности."""

    schema_version: str = "1.0"
    sample: str = ""
    operator: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    comment: str = ""
    channels: list[ChannelDescriptor] = Field(default_factory=list)


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
        if self.data:
            first_col = next(iter(self.data.values()))
            return first_col
        return np.array([], dtype=float)

    def get_channel(self, column: str) -> np.ndarray | None:
        return self.data.get(column)

    def get_descriptor(self, column: str) -> ChannelDescriptor | None:
        for ch in self.manifest.channels:
            if ch.column == column:
                return ch
        return None

    def to_dataframe(self) -> pd.DataFrame:
        df = pd.DataFrame()
        ordered_cols = [
            ch.column for ch in self.manifest.channels if ch.column in self.data
        ]
        for col in ordered_cols:
            df[col] = self.data[col]
        for col, arr in self.data.items():
            if col not in df.columns:
                df[col] = arr
        return df
