"""Adjustments.

The flow: set the values in the preview -> save them as a preset -> apply
them to other photos in bulk -> have them take effect on export.

The parameter definitions are in settings.py and the actual pixel work is
in engine.py. The preview and the export use the same
engine.apply_settings.
"""

from __future__ import annotations

from .settings import (
    EXIF_FIELDS,
    HSL_BAND_CENTERS,
    HSL_BAND_LABELS,
    HSL_BANDS,
    NOISE_ALGORITHM_LABELS,
    STRIP_FIELDS,
    BasicSettings,
    ColorGradeSettings,
    ColorGradeZone,
    CropRatio,
    CurveSettings,
    DetailSettings,
    DevelopSettings,
    EffectSettings,
    ExifStripSettings,
    GeometrySettings,
    HSLBand,
    HSLSettings,
    LocalAdjustments,
    Mask,
    MaskCombine,
    MaskType,
    MetadataSettings,
    NoiseAlgorithm,
    OpticsSettings,
    WatermarkPosition,
    WatermarkSettings,
)

__all__ = [
    "DevelopSettings",
    "BasicSettings",
    "CurveSettings",
    "DetailSettings",
    "NoiseAlgorithm",
    "NOISE_ALGORITHM_LABELS",
    "HSLSettings",
    "HSLBand",
    "ColorGradeSettings",
    "ColorGradeZone",
    "EffectSettings",
    "GeometrySettings",
    "CropRatio",
    "WatermarkSettings",
    "WatermarkPosition",
    "MetadataSettings",
    "OpticsSettings",
    "ExifStripSettings",
    "Mask",
    "MaskCombine",
    "MaskType",
    "LocalAdjustments",
    "EXIF_FIELDS",
    "STRIP_FIELDS",
    "HSL_BANDS",
    "HSL_BAND_LABELS",
    "HSL_BAND_CENTERS",
]
