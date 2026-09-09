from __future__ import annotations

import argparse
import ctypes
import json
import math
import os

from uga.environment.fixture_world import (
    FixtureMode,
    FixtureScenario,
    FixtureSnapshot,
    FixtureWorld,
)
from uga.time.clock import PerfCounterClock


def _headless_smoke(scenario: FixtureScenario) -> None:
    world = FixtureWorld(scenario=scenario)
    for key in world.movement_keys:
        world.set_key(key, True)
    for _ in range(300):
        if world.snapshot.distance_to_target <= 25:
            break
        world.tick(16_666_667)
    for key in world.movement_keys:
        world.set_key(key, False)
    world.set_key("e", True)
    snapshot = world.snapshot
    print(
        json.dumps(
            {
                "mode": snapshot.mode.value,
                "scenario": scenario.value,
                "success": snapshot.success,
                "player_x": round(snapshot.player_x, 2),
                "distance_to_target": round(snapshot.distance_to_target, 2),
            },
            sort_keys=True,
        )
    )


def _run_window(scenario: FixtureScenario) -> None:
    import tkinter as tk

    world = FixtureWorld(scenario=scenario)
    clock = PerfCounterClock()
    root = tk.Tk()
    root.title(world.window_title)
    root.geometry(f"{world.width}x{world.height}")
    root.resizable(True, True)
    canvas = tk.Canvas(root, highlightthickness=0, background="#111827")
    canvas.pack(fill="both", expand=True)
    root.lift()
    root.focus_force()
    root.after(250, lambda: (root.lift(), root.focus_force()))
    previous_mouse_x: int | None = None
    last_tick = clock.now()
    user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
    if user32 is not None:
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
    polled_keys = {
        "w": 0x57,
        "a": 0x41,
        "s": 0x53,
        "d": 0x44,
        "e": 0x45,
        "f": 0x46,
        "r": 0x52,
        "escape": 0x1B,
    }

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
        canvas.focus_set()
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
                f"Scenario: {world.scenario.value}\n"
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

    def poll_keys() -> None:
        if user32 is None:
            return
        for name, virtual_key in polled_keys.items():
            world.set_key(name, bool(user32.GetAsyncKeyState(virtual_key) & 0x8000))

    def loop() -> None:
        nonlocal last_tick
        now = clock.now()
        poll_keys()
        snapshot = world.tick(now.value_ns - last_tick.value_ns)
        last_tick = now
        render(snapshot)
        root.after(16, loop)

    if user32 is None:
        root.bind_all("<KeyPress>", key_down)
        root.bind_all("<KeyRelease>", key_up)
    canvas.bind("<Motion>", mouse_move)
    canvas.bind("<Button-1>", click)
    canvas.focus_set()
    loop()
    root.mainloop()


def _run_focus_sink() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("UGA Focus Sink")
    root.geometry("320x120")
    root.resizable(False, False)
    label = tk.Label(
        root,
        text="Developer-owned focus-loss qualification window",
        background="#172554",
        foreground="white",
        font=("Segoe UI", 11),
    )
    label.pack(fill="both", expand=True)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the developer-owned UGA fixture world")
    parser.add_argument("--headless-smoke", action="store_true")
    parser.add_argument("--focus-sink", action="store_true")
    parser.add_argument(
        "--scenario",
        choices=tuple(item.value for item in FixtureScenario),
        default=FixtureScenario.EXPLORATION.value,
    )
    args = parser.parse_args()
    scenario = FixtureScenario(args.scenario)
    if args.headless_smoke:
        _headless_smoke(scenario)
    elif args.focus_sink:
        _run_focus_sink()
    else:
        _run_window(scenario)


if __name__ == "__main__":
    main()
