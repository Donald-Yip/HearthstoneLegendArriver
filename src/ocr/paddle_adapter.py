"""Lazy PaddleOCR boundary with normalized evidence output."""

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
import time

from src.recommendation_models import OcrEvidence, OcrLine


class OcrUnavailableError(RuntimeError):
    pass


def _external_click_module():
    """Load PyPI's click package instead of this project's click.py."""
    project_root = os.path.normcase(str(Path(__file__).resolve().parents[2]))
    search_path = [
        entry for entry in sys.path
        if entry and os.path.normcase(os.path.abspath(entry)) != project_root
    ]
    spec = importlib.machinery.PathFinder.find_spec("click", search_path)
    if spec is None or spec.loader is None or not spec.origin:
        raise OcrUnavailableError("external click package not found")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get("click")
    sys.modules["click"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is not None:
            sys.modules["click"] = previous
        else:
            sys.modules.pop("click", None)
    return module


class PaddleOcrAdapter:
    name = "paddleocr"

    def __init__(self, engine=None, clock=time.time, engine_factory=None,
                 model_root=None):
        self.engine = engine
        self.clock = clock
        self.engine_factory = engine_factory
        default_root = (Path(os.environ.get("LOCALAPPDATA", Path.home()))
                        / "AutoHS" / "ocr_models" / "paddleocr")
        self.model_root = Path(model_root or default_root).resolve()

    def load(self):
        if self.engine is not None:
            return self
        try:
            project_click = sys.modules.get("click")
            sys.modules["click"] = _external_click_module()
            if self.engine_factory is None:
                from paddleocr import PaddleOCR
                self.engine_factory = PaddleOCR
            self.model_root.mkdir(parents=True, exist_ok=True)
            self.engine = self.engine_factory(
                use_angle_cls=False, lang="ch", show_log=False,
                det_model_dir=str(self.model_root / "det_ch_ppocrv4"),
                rec_model_dir=str(self.model_root / "rec_ch_ppocrv4"),
                cls_model_dir=str(self.model_root / "cls_ch_mobile_v2"),
            )
        except Exception as exc:
            raise OcrUnavailableError(
                f"PaddleOCR unavailable: {type(exc).__name__}: {exc}") from exc
        finally:
            if "project_click" in locals() and project_click is not None:
                sys.modules["click"] = project_click
            elif "project_click" in locals():
                sys.modules.pop("click", None)
        return self

    def recognize(self, image, frame_id, preprocessing):
        self.load()
        try:
            raw = self.engine.ocr(image, cls=False)
        except Exception as exc:
            raise OcrUnavailableError(
                f"PaddleOCR recognition failed: {type(exc).__name__}: {exc}") from exc
        lines = []
        for page in raw or []:
            for item in page or []:
                box, pair = item
                text, confidence = pair
                lines.append(OcrLine(
                    str(text).strip(), float(confidence),
                    tuple((float(x), float(y)) for x, y in box)))
        lines.sort(key=lambda item: min(
            (point[1] for point in item.box), default=0.0))
        normalized = "\n".join(line.text for line in lines if line.text)
        confidence = min((line.confidence for line in lines), default=0.0)
        return OcrEvidence(
            frame_id, self.clock(), tuple(lines), normalized, confidence,
            self.name, preprocessing)

