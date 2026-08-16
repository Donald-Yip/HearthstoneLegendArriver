"""Versioned image preprocessing candidates for HSAng's dark panel."""

import cv2
import numpy as np


def iter_preprocess_recommendation(image: np.ndarray):
    """Generate OCR candidates lazily, stopping work after a successful one."""
    scaled = cv2.resize(
        image, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    yield "scaled_color_v1", scaled
    gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    yield "gray_clahe_v1", clahe
    binary = cv2.inRange(scaled, (105, 105, 105), (255, 255, 255))
    yield "light_text_binary_v1", binary


def preprocess_recommendation(image: np.ndarray) -> dict[str, np.ndarray]:
    """Compatibility wrapper for callers that require every candidate."""
    return dict(iter_preprocess_recommendation(image))
