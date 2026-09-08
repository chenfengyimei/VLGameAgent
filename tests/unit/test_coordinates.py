from __future__ import annotations

import unittest

from uga.core.errors import ContractViolation
from uga.windows.coordinates import CoordinateSpace, CoordinateTransform, Point, Rect


class CoordinateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transform = CoordinateTransform(
            image_rect=Rect(0, 0, 1920, 1080),
            image_content_rect=Rect(240, 0, 1680, 1080),
            client_screen_rect=Rect(-1920, 100, 0, 1180),
            window_screen_rect=Rect(-1930, 70, 10, 1190),
            dpi_scale=1.5,
        )

    def test_normalized_center_ignores_letterbox_and_handles_negative_monitor(self) -> None:
        result = self.transform.convert(
            Point(0.5, 0.5),
            CoordinateSpace.MODEL_NORMALIZED,
            CoordinateSpace.PHYSICAL_SCREEN_PIXEL,
        )
        self.assertEqual(result, Point(-960, 640))

    def test_client_physical_round_trip(self) -> None:
        point = Point(300, 400)
        physical = self.transform.convert(
            point, CoordinateSpace.CLIENT_PIXEL, CoordinateSpace.PHYSICAL_SCREEN_PIXEL
        )
        self.assertEqual(
            self.transform.convert(
                physical, CoordinateSpace.PHYSICAL_SCREEN_PIXEL, CoordinateSpace.CLIENT_PIXEL
            ),
            point,
        )

    def test_logical_to_physical_applies_dpi(self) -> None:
        self.assertEqual(
            self.transform.convert(
                Point(-100, 20),
                CoordinateSpace.LOGICAL_SCREEN_PIXEL,
                CoordinateSpace.PHYSICAL_SCREEN_PIXEL,
            ),
            Point(-150, 30),
        )

    def test_out_of_range_model_coordinate_rejected(self) -> None:
        with self.assertRaises(ContractViolation):
            self.transform.convert(
                Point(1.1, 0.5),
                CoordinateSpace.MODEL_NORMALIZED,
                CoordinateSpace.IMAGE_PIXEL,
            )


if __name__ == "__main__":
    unittest.main()
