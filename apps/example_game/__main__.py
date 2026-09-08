from __future__ import annotations

import argparse
import json
import math

from uga.environment.fixture_world import FixtureMode, FixtureSnapshot, FixtureWorld
from uga.time.clock import PerfCounterClock


def _headless_smoke() -> None:
    world = FixtureWorld()
    world.set_key("d", True)
    for _ in range(165):
        world.tick(16_666_667)
    world.set_key("d", False)
    world.set_key("e", True)
    snapshot = world.snapshot
    print(
        json.dumps(
            {
                "mode": snapshot.mode.value,
                "success": snapshot.success,
                "player_x": round(snapshot.player_x, 2),
                "distance_to_target": round(snapshot.distance_to_target, 2),
            },
            sort_keys=True,
        )
    )


def _run_window() -> None:
    import tkinter as tk

    world = FixtureWorld()
    clock = PerfCounterClock()
    root = tk.Tk()
    root.title("UGA Fixture World")
    root.geometry(f"{world.width}x{world.height}")
    root.resizable(True, True)
    canvas = tk.Canvas(root, highlightthickness=0, background="#111827")
    canvas.pack(fill="both", expand=True)
    previous_mouse_x: int | None = None
    last_tick = clock.now()

    def key_down(event: tk.Event[tk.Misc]) -> None:
        world.set_key(str(event.keysym), True)

    def key_up(event: tk.Event[tk.Misc]) -> None:
        world.set_key(str(event.keysym), False)

    def mouse_move(event: tk.Event[tk.Misc]) -> None:
        nonlocal previous_mouse_x
        x = int(event.x)
        if previous_mouse_x is not None:
            world.add_mouse_delta(x - previous_mouse_x)
        previous_mouse_x = x

    def click(event: tk.Event[tk.Misc]) -> None:
        world.click(
            float(event.x) * world.width / max(canvas.winfo_width(), 1),
            float(event.y) * world.height / max(canvas.winfo_height(), 1),
        )

    def render(snapshot: FixtureSnapshot) -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        scale_x = width / world.width
        scale_y = height / world.height
        for x in range(0, world.width, 80):
            canvas.create_line(x * scale_x, 0, x * scale_x, height, fill="#1f2937")
        for y in range(0, world.height, 80):
            canvas.create_line(0, y * scale_y, width, y * scale_y, fill="#1f2937")
        tx, ty = snapshot.target_x * scale_x, snapshot.target_y * scale_y
        px, py = snapshot.player_x * scale_x, snapshot.player_y * scale_y
        canvas.create_oval(tx - 28, ty - 28, tx + 28, ty + 28, fill="#22c55e", outline="white")
        canvas.create_text(tx, ty - 45, text="TARGET", fill="white", font=("Segoe UI", 12, "bold"))
        canvas.create_oval(px - 18, py - 18, px + 18, py + 18, fill="#38bdf8", outline="white")
        canvas.create_line(
            px,
            py,
            px + math.cos(snapshot.heading_radians) * 36,
            py + math.sin(snapshot.heading_radians) * 36,
            fill="#f8fafc",
            width=4,
            arrow=tk.LAST,
        )
        canvas.create_text(
            16,
            16,
            anchor="nw",
            text=(
                "UGA FIXTURE WORLD\n"
                "Goal: move to the green target and press E\n"
                "WASD move · mouse turns · Esc menu · R reset\n"
                f"Mode: {snapshot.mode.value} · Distance: {snapshot.distance_to_target:.1f}"
            ),
            fill="#f8fafc",
            font=("Consolas", 12),
        )
        if snapshot.mode == FixtureMode.GUI:
            canvas.create_rectangle(
                width / 2 - 180,
                height / 2 - 120,
                width / 2 + 180,
                height / 2 + 120,
                fill="#0f172a",
                outline="#94a3b8",
                width=3,
            )
            canvas.create_text(width / 2, height / 2 - 85, text="PAUSED / GUI", fill="white")
            canvas.create_rectangle(
                width / 2 - 100,
                height / 2 - 45,
                width / 2 + 100,
                height / 2 + 5,
                fill="#2563eb",
            )
            canvas.create_text(width / 2, height / 2 - 20, text="Resume", fill="white")
            canvas.create_rectangle(
                width / 2 - 100,
                height / 2 + 20,
                width / 2 + 100,
                height / 2 + 70,
                fill="#475569",
            )
            canvas.create_text(width / 2, height / 2 + 45, text="Reset", fill="white")
        elif snapshot.success:
            canvas.create_text(
                width / 2,
                height / 2,
                text="SUCCESS\nPress R to reset",
                fill="#facc15",
                font=("Segoe UI", 28, "bold"),
                justify="center",
            )

    def loop() -> None:
        nonlocal last_tick
        now = clock.now()
        snapshot = world.tick(now.value_ns - last_tick.value_ns)
        last_tick = now
        render(snapshot)
        root.after(16, loop)

    root.bind("<KeyPress>", key_down)
    root.bind("<KeyRelease>", key_up)
    canvas.bind("<Motion>", mouse_move)
    canvas.bind("<Button-1>", click)
    canvas.focus_set()
    loop()
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the developer-owned UGA fixture world")
    parser.add_argument("--headless-smoke", action="store_true")
    args = parser.parse_args()
    if args.headless_smoke:
        _headless_smoke()
    else:
        _run_window()


if __name__ == "__main__":
    main()
