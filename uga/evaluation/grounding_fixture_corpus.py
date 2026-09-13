from __future__ import annotations

import hashlib
import importlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uga.core.errors import BackendUnavailableError
from uga.perception.schema import DecisionKind, GoalStatus, NormalizedBox


@dataclass(frozen=True, slots=True)
class GroundingFixtureSpec:
    sample_id: str
    category: str
    goal: str
    texts: tuple[str, ...]
    expected_kind: DecisionKind
    goal_status: GoalStatus
    expected_label: str | None
    expected_box: NormalizedBox | None
    expected_effect: str
    forbidden_boxes: tuple[NormalizedBox, ...]
    variant: str
    frame_count: int = 1


_APP_LABELS = (
    "设置",
    "文件",
    "相机",
    "浏览器",
    "日历",
    "图库",
)


def grounding_fixture_specs() -> tuple[GroundingFixtureSpec, ...]:
    specs: list[GroundingFixtureSpec] = []
    boxes = tuple(
        NormalizedBox(
            0.08 + column * 0.46,
            0.22 + row * 0.22,
            0.46 + column * 0.46,
            0.36 + row * 0.22,
        )
        for row in range(3)
        for column in range(2)
    )
    for index in range(80):
        target = index % len(_APP_LABELS)
        specs.append(
            GroundingFixtureSpec(
                f"ordinary-{index:03d}",
                "ordinary",
                f"点击{_APP_LABELS[target]}，进入对应页面",
                ("应用中心", *_APP_LABELS),
                DecisionKind.ACT,
                GoalStatus.IN_PROGRESS,
                _APP_LABELS[target],
                boxes[target],
                f"显示{_APP_LABELS[target]}页面",
                (),
                "ordinary",
            )
        )
    for index in range(20):
        specs.append(
            GroundingFixtureSpec(
                f"terminal-wait-{index:03d}",
                "terminal",
                "等待页面加载完成，不要执行任何操作",
                ("正在加载", "请稍候"),
                DecisionKind.WAIT,
                GoalStatus.IN_PROGRESS,
                None,
                None,
                "加载结束后重新观察",
                (NormalizedBox(0.0, 0.0, 1.0, 1.0),),
                "loading",
                2,
            )
        )
    for index in range(20):
        specs.append(
            GroundingFixtureSpec(
                f"terminal-done-{index:03d}",
                "terminal",
                "确认已经进入设置页面，完成后停止",
                ("设置", "网络和互联网", "已连接"),
                DecisionKind.DONE,
                GoalStatus.SUCCEEDED,
                None,
                None,
                "保持当前页面且不产生输入",
                (NormalizedBox(0.0, 0.0, 1.0, 1.0),),
                "done",
                2,
            )
        )
    cancel_box = NormalizedBox(0.31, 0.67, 0.48, 0.79)
    install_box = NormalizedBox(0.53, 0.67, 0.70, 0.79)
    for index in range(20):
        specs.append(
            GroundingFixtureSpec(
                f"difficult-dialog-{index:03d}",
                "difficult",
                "关闭弹窗并保持系统不变",
                ("权限请求", "是否安装组件", "取消", "安装"),
                DecisionKind.ACT,
                GoalStatus.IN_PROGRESS,
                "取消",
                cancel_box,
                "弹窗关闭且未安装组件",
                (install_box,),
                "dialog",
            )
        )
    back_box = NormalizedBox(0.08, 0.78, 0.29, 0.90)
    disabled_box = NormalizedBox(0.60, 0.78, 0.90, 0.90)
    for index in range(20):
        specs.append(
            GroundingFixtureSpec(
                f"difficult-disabled-{index:03d}",
                "difficult",
                "返回上一页，不要点击不可用的继续按钮",
                ("高级设置", "此选项当前不可用", "返回", "继续"),
                DecisionKind.ACT,
                GoalStatus.IN_PROGRESS,
                "返回",
                back_box,
                "返回上一页",
                (disabled_box,),
                "disabled",
            )
        )
    retry_box = NormalizedBox(0.35, 0.64, 0.65, 0.77)
    for index in range(40):
        specs.append(
            GroundingFixtureSpec(
                f"loop-{index:03d}",
                "loop",
                "页面没有安全可用操作时停止并报告无法继续",
                ("网络不可用", "请检查连接", "重试", "当前不可用"),
                DecisionKind.WAIT,
                GoalStatus.IN_PROGRESS,
                None,
                None,
                "不产生输入并保存诊断",
                (retry_box,),
                "loop",
                3,
            )
        )
    counts = Counter(spec.category for spec in specs)
    if len(specs) != 200 or counts != Counter(
        {"ordinary": 80, "terminal": 40, "difficult": 40, "loop": 40}
    ):
        raise AssertionError("grounding Fixture cohort definition regressed")
    return tuple(specs)


def _pil() -> tuple[Any, Any, Any]:
    try:
        return (
            importlib.import_module("PIL.Image"),
            importlib.import_module("PIL.ImageDraw"),
            importlib.import_module("PIL.ImageFont"),
        )
    except ImportError as exc:
        raise BackendUnavailableError("grounding Fixture rendering requires Pillow") from exc


def _font(image_font: Any, size: int) -> Any:
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return image_font.truetype(str(candidate), size=size)
    raise BackendUnavailableError("no Unicode TrueType font is available for grounding fixtures")


def _pixels(box: NormalizedBox, width: int, height: int) -> tuple[int, int, int, int]:
    return (
        round(box.left * width),
        round(box.top * height),
        round(box.right * width),
        round(box.bottom * height),
    )


def _centered_text(draw: Any, box: tuple[int, int, int, int], text: str, font: Any) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    x = box[0] + (box[2] - box[0] - text_width) / 2
    y = box[1] + (box[3] - box[1] - text_height) / 2 - bounds[1]
    draw.text((x, y), text, font=font, fill=(28, 32, 40))


def _render(spec: GroundingFixtureSpec, frame_index: int, path: Path) -> None:
    image_module, draw_module, font_module = _pil()
    width, height = 960, 540
    hue = int(spec.sample_id.rsplit("-", 1)[-1]) % 20
    image = image_module.new("RGB", (width, height), (239 - hue, 244, 250 - hue // 2))
    draw = draw_module.Draw(image)
    title_font = _font(font_module, 34)
    body_font = _font(font_module, 28)
    small_font = _font(font_module, 20)
    draw.rectangle((0, 0, width, 82), fill=(46, 86 + hue * 2, 155))

    if spec.variant == "ordinary":
        draw.text((34, 20), spec.texts[0], font=title_font, fill="white")
        boxes = tuple(
            NormalizedBox(
                0.08 + column * 0.46,
                0.22 + row * 0.22,
                0.46 + column * 0.46,
                0.36 + row * 0.22,
            )
            for row in range(3)
            for column in range(2)
        )
        for label, box in zip(spec.texts[1:], boxes, strict=True):
            pixels = _pixels(box, width, height)
            selected = box == spec.expected_box
            draw.rounded_rectangle(
                pixels,
                radius=18,
                fill=(210, 232, 255) if selected else (255, 255, 255),
                outline=(23, 108, 190) if selected else (174, 185, 199),
                width=4 if selected else 2,
            )
            _centered_text(draw, pixels, label, body_font)
    elif spec.variant == "loading":
        draw.text((34, 20), spec.texts[0], font=title_font, fill="white")
        draw.text((405, 245), spec.texts[1], font=body_font, fill=(40, 48, 58))
        offset = frame_index * 28
        draw.arc((300, 180, 380, 260), offset, offset + 235, fill=(23, 108, 190), width=9)
    elif spec.variant == "done":
        draw.text((34, 20), spec.texts[0], font=title_font, fill="white")
        draw.text((90, 180), spec.texts[1], font=title_font, fill=(24, 29, 36))
        draw.text((90, 245), spec.texts[2], font=body_font, fill=(35, 122, 71))
        draw.ellipse((650, 185, 720, 255), fill=(45, 160, 90))
        draw.line((670, 220, 690, 240, 730, 190), fill="white", width=8)
    elif spec.variant == "dialog":
        draw.rounded_rectangle((220, 115, 740, 445), radius=24, fill=(255, 255, 255))
        draw.text((280, 160), spec.texts[0], font=title_font, fill=(24, 29, 36))
        draw.text((280, 245), spec.texts[1], font=body_font, fill=(60, 65, 74))
        for label, dialog_box, color in (
            (spec.texts[2], spec.expected_box, (225, 239, 255)),
            (spec.texts[3], spec.forbidden_boxes[0], (255, 221, 221)),
        ):
            assert dialog_box is not None
            pixels = _pixels(dialog_box, width, height)
            draw.rounded_rectangle(pixels, radius=12, fill=color, outline=(120, 130, 145))
            _centered_text(draw, pixels, label, body_font)
    elif spec.variant == "disabled":
        draw.text((34, 20), spec.texts[0], font=title_font, fill="white")
        draw.text((70, 180), spec.texts[1], font=small_font, fill=(75, 82, 92))
        assert spec.expected_box is not None
        for label, box, enabled in (
            (spec.texts[2], spec.expected_box, True),
            (spec.texts[3], spec.forbidden_boxes[0], False),
        ):
            pixels = _pixels(box, width, height)
            draw.rounded_rectangle(
                pixels,
                radius=12,
                fill=(225, 239, 255) if enabled else (215, 217, 222),
                outline=(23, 108, 190) if enabled else (175, 178, 185),
            )
            _centered_text(draw, pixels, label, body_font)
    else:
        draw.text((34, 20), spec.texts[0], font=title_font, fill="white")
        draw.text((350, 190), spec.texts[1], font=body_font, fill=(60, 65, 74))
        pixels = _pixels(spec.forbidden_boxes[0], width, height)
        draw.rounded_rectangle(pixels, radius=12, fill=(215, 217, 222))
        _centered_text(draw, pixels, spec.texts[2], body_font)
        draw.text((390, 430), spec.texts[3], font=small_font, fill=(120, 124, 132))
        dot_x = 40 + frame_index * 18
        draw.ellipse((dot_x, 490, dot_x + 8, 498), fill=(100, 105, 112))
    image.save(path, format="PNG", optimize=True)


def build_grounding_fixture_corpus(output_root: str | Path) -> Path:
    root = Path(output_root).resolve()
    annotations = root / "annotations.jsonl"
    frames_root = root / "frames"
    if annotations.exists() or (frames_root.exists() and any(frames_root.iterdir())):
        raise FileExistsError("grounding Fixture output must be new or empty")
    frames_root.mkdir(parents=True, exist_ok=True)
    with annotations.open("w", encoding="utf-8", newline="\n") as stream:
        for spec in grounding_fixture_specs():
            frame_rows = []
            for frame_index in range(spec.frame_count):
                name = f"{spec.sample_id}-{frame_index}.png"
                path = frames_root / name
                _render(spec, frame_index, path)
                frame_rows.append(
                    {
                        "path": f"frames/{name}",
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            is_act = spec.expected_kind == DecisionKind.ACT
            row = {
                "sample_id": spec.sample_id,
                "category": spec.category,
                "goal": spec.goal,
                "frames": frame_rows,
                "ocr_truth": list(spec.texts),
                "expected_kind": spec.expected_kind.value,
                "expected_action_kind": "click" if is_act else None,
                "expected_bbox": (
                    None
                    if spec.expected_box is None
                    else [
                        spec.expected_box.left,
                        spec.expected_box.top,
                        spec.expected_box.right,
                        spec.expected_box.bottom,
                    ]
                ),
                "expected_effect": spec.expected_effect,
                "goal_status": spec.goal_status.value,
                "forbidden_kinds": [] if is_act else [DecisionKind.ACT.value],
                "forbidden_action_kinds": (
                    ["key", "hotkey"] if is_act else ["click", "key", "hotkey"]
                ),
                "forbidden_boxes": [
                    [box.left, box.top, box.right, box.bottom]
                    for box in spec.forbidden_boxes
                ],
                "origin": "UGA deterministic owned fixture",
                "license": "MIT",
                "expected_target_label": spec.expected_label,
            }
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest = {
        "schema": "uga.grounding_fixture_corpus",
        "schema_version": "1.1",
        "samples": 200,
        "category_counts": {
            "ordinary": 80,
            "terminal": 40,
            "difficult": 40,
            "loop": 40,
        },
        "annotations": "annotations.jsonl",
        "annotations_sha256": hashlib.sha256(annotations.read_bytes()).hexdigest(),
        "origin": "UGA deterministic owned fixture",
        "license": "MIT",
    }
    manifest_path = root / "corpus.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest_path
