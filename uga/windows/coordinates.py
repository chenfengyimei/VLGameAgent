from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from uga.core.errors import ContractViolation


class CoordinateSpace(StrEnum):
    MODEL_NORMALIZED = "model_normalized"
    IMAGE_PIXEL = "image_pixel"
    CLIENT_PIXEL = "client_pixel"
    WINDOW_PIXEL = "window_pixel"
    PHYSICAL_SCREEN_PIXEL = "physical_screen_pixel"
    LOGICAL_SCREEN_PIXEL = "logical_screen_pixel"


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rect:
    """Half-open rectangle; screen origins may be negative on secondary displays."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if self.right <= self.left or self.bottom <= self.top:
            raise ContractViolation("rectangle must have positive width and height")

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class CoordinateTransform:
    """Explicit conversion context for one captured window generation."""

    image_rect: Rect
    image_content_rect: Rect
    client_screen_rect: Rect
    window_screen_rect: Rect
    dpi_scale: float

    def __post_init__(self) -> None:
        if self.dpi_scale <= 0:
            raise ContractViolation("DPI scale must be positive")
        content = self.image_content_rect
        image = self.image_rect
        if (
            content.left < image.left
            or content.top < image.top
            or content.right > image.right
            or content.bottom > image.bottom
        ):
            raise ContractViolation("image content rect must be contained by image rect")

    def convert(self, point: Point, source: CoordinateSpace, target: CoordinateSpace) -> Point:
        if source == target:
            return point
        physical = self._to_physical(point, source)
        return self._from_physical(physical, target)

    def _to_physical(self, point: Point, source: CoordinateSpace) -> Point:
        client = self.client_screen_rect
        window = self.window_screen_rect
        content = self.image_content_rect
        if source == CoordinateSpace.MODEL_NORMALIZED:
            if not 0.0 <= point.x <= 1.0 or not 0.0 <= point.y <= 1.0:
                raise ContractViolation("model-normalized coordinates must be in [0, 1]")
            image_point = Point(
                content.left + point.x * content.width,
                content.top + point.y * content.height,
            )
            return self._to_physical(image_point, CoordinateSpace.IMAGE_PIXEL)
        if source == CoordinateSpace.IMAGE_PIXEL:
            normalized_x = (point.x - content.left) / content.width
            normalized_y = (point.y - content.top) / content.height
            return Point(
                client.left + normalized_x * client.width,
                client.top + normalized_y * client.height,
            )
        if source == CoordinateSpace.CLIENT_PIXEL:
            return Point(client.left + point.x, client.top + point.y)
        if source == CoordinateSpace.WINDOW_PIXEL:
            return Point(window.left + point.x, window.top + point.y)
        if source == CoordinateSpace.PHYSICAL_SCREEN_PIXEL:
            return point
        if source == CoordinateSpace.LOGICAL_SCREEN_PIXEL:
            return Point(point.x * self.dpi_scale, point.y * self.dpi_scale)
        raise ContractViolation(f"unsupported source coordinate space: {source}")

    def _from_physical(self, point: Point, target: CoordinateSpace) -> Point:
        client = self.client_screen_rect
        window = self.window_screen_rect
        content = self.image_content_rect
        if target == CoordinateSpace.PHYSICAL_SCREEN_PIXEL:
            return point
        if target == CoordinateSpace.LOGICAL_SCREEN_PIXEL:
            return Point(point.x / self.dpi_scale, point.y / self.dpi_scale)
        if target == CoordinateSpace.CLIENT_PIXEL:
            return Point(point.x - client.left, point.y - client.top)
        if target == CoordinateSpace.WINDOW_PIXEL:
            return Point(point.x - window.left, point.y - window.top)
        normalized = Point(
            (point.x - client.left) / client.width,
            (point.y - client.top) / client.height,
        )
        if target == CoordinateSpace.MODEL_NORMALIZED:
            return normalized
        if target == CoordinateSpace.IMAGE_PIXEL:
            return Point(
                content.left + normalized.x * content.width,
                content.top + normalized.y * content.height,
            )
        raise ContractViolation(f"unsupported target coordinate space: {target}")
