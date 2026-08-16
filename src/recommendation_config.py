"""Conservative configuration for the first recommendation automation release."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RecommendationConfig:
    desktop_size: tuple[int, int] = (1920, 1080)
    desktop_dpi: int = 96
    recommendation_roi: tuple[int, int, int, int] = (7, 32, 278, 970)
    max_attempts: int = 3
    stable_frames: int = 2
    min_ocr_confidence: float = 0.85
    retry_interval_seconds: float = 0.1
    mulligan_ready_delay_seconds: float = 20.0
    recognition_timeout_seconds: float = 2.0
    result_timeout_seconds: float = 5.0
