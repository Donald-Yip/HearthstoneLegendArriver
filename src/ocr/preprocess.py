"""Versioned image preprocessing candidates for HSAng's dark panel."""

import os

import cv2
import numpy as np

from src.recommendation_config import RecommendationConfig

# 固定缩放回退（config 默认 1.5x）。当 HS_ADAPTIVE_OCR_SCALE 未开启时使用，
# 保证当前参考布局（1920x1080 @ 100%）的行为完全不变。
_DEFAULT_SCALE = float(RecommendationConfig().ocr_preprocess_scale)

# 目标行高：把盒子面板里的文字行归一化到参考布局 1.5x 下的行高（约 30px），
# 使不同电脑/不同 UI 缩放下，送入 OCR 的文字尺寸一致，从而识别更稳。
_TARGET_LINE_HEIGHT = 30.0


def _estimate_scale(image: np.ndarray, default: float) -> float:
    """按 ROI 内文本行高估算最优放大倍数，归一化文字尺寸。

    优先级：
      1. 环境变量 OCR_PREPROCESS_SCALE：强制固定缩放（老行为/调试）。
      2. 环境变量 HS_ADAPTIVE_OCR_SCALE=1：启用自适应（默认关闭）。
      3. 回退默认缩放（config.ocr_preprocess_scale）。

    图片无文本/过小/无法可靠估计时回退默认缩放，绝不抛错。
    """
    fixed = os.environ.get("OCR_PREPROCESS_SCALE")
    if fixed:
        return float(fixed)
    if os.environ.get("HS_ADAPTIVE_OCR_SCALE", "0") != "1":
        return default

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if gray.size == 0 or gray.shape[0] < 4 or gray.shape[1] < 4:
        return default
    _, binary = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY)
    row_sum = binary.sum(axis=1) / 255.0
    # 一行至少占整行宽度的 5%，避免噪声被当成文本行。
    active = row_sum > max(2.0, binary.shape[1] * 0.05)

    heights = []
    run = 0
    for flag in active:
        if flag:
            run += 1
        elif run:
            heights.append(run)
            run = 0
    if run:
        heights.append(run)
    if not heights:
        return default
    line_height = float(np.median(heights))
    if line_height <= 0:
        return default
    scale = _TARGET_LINE_HEIGHT / line_height
    return float(np.clip(scale, 0.8, 3.0))


def iter_preprocess_recommendation(image: np.ndarray):
    """Generate OCR candidates lazily, stopping work after a successful one."""
    scale = _estimate_scale(image, _DEFAULT_SCALE)
    scaled = cv2.resize(
        image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    yield "scaled_color_v1", scaled
    gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    yield "gray_clahe_v1", clahe
    binary = cv2.inRange(scaled, (105, 105, 105), (255, 255, 255))
    yield "light_text_binary_v1", binary


def preprocess_recommendation(image: np.ndarray) -> dict[str, np.ndarray]:
    """Compatibility wrapper for callers that require every candidate."""
    return dict(iter_preprocess_recommendation(image))
