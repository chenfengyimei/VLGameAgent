from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

from uga.capture.frame import Frame
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.perception.schema import NormalizedBox, TextRegion
from uga.policy.vlm_planner import encode_frame_png


@runtime_checkable
class TextObservationProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def recognize(self, frame: Frame) -> tuple[TextRegion, ...]: ...


class NullTextProvider:
    @property
    def available(self) -> bool:
        return False

    def recognize(self, frame: Frame) -> tuple[TextRegion, ...]:
        frame.validate()
        return ()


class RapidOcrProvider:
    """Lazy adapter for RapidOCR 3.x; the core runtime stays dependency-light."""

    def __init__(self, engine: Callable[[bytes], object] | None = None) -> None:
        if engine is None:
            try:
                module = importlib.import_module("rapidocr")
                engine_type = module.RapidOCR
                engine = engine_type()
            except (ImportError, AttributeError, RuntimeError) as exc:
                raise BackendUnavailableError(
                    "RapidOCR is unavailable; install the locked vision dependency set"
                ) from exc
        self._engine = engine

    @property
    def available(self) -> bool:
        return True

    def recognize(self, frame: Frame) -> tuple[TextRegion, ...]:
        result = self._engine(encode_frame_png(frame, max_width=max(frame.width, 32)))
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if boxes is None and isinstance(result, Sequence) and len(result) >= 3:
            boxes, texts, scores = result[:3]
        if boxes is None or texts is None or scores is None:
            return ()
        try:
            triples = zip(boxes, texts, scores, strict=True)
        except TypeError as exc:
            raise ContractViolation("RapidOCR returned malformed result arrays") from exc
        regions: list[TextRegion] = []
        try:
            for points, text, score in triples:
                normalized = self._normalize_box(points, frame.width, frame.height)
                confidence = float(score)
                value = str(text).strip()
                if value and confidence >= 0.0:
                    regions.append(TextRegion(value, normalized, min(confidence, 1.0)))
        except (TypeError, ValueError, IndexError) as exc:
            raise ContractViolation("RapidOCR returned malformed boxes or scores") from exc
        return tuple(regions)

    @staticmethod
    def _normalize_box(points: Any, width: int, height: int) -> NormalizedBox:
        coordinates = [(float(point[0]), float(point[1])) for point in points]
        if len(coordinates) < 2:
            raise ContractViolation("OCR box requires at least two points")
        xs = [point[0] for point in coordinates]
        ys = [point[1] for point in coordinates]
        left = max(0.0, min(xs) / width)
        top = max(0.0, min(ys) / height)
        right = min(1.0, max(xs) / width)
        bottom = min(1.0, max(ys) / height)
        return NormalizedBox(left, top, right, bottom)
