"""
Record docs/demo.gif: drive ewstui --demo headlessly (Textual's test pilot),
screenshot each step of a storyboard, then turn the screenshots into a GIF.

    uv run python scripts/demo_gif.py [output.gif]

Needs rsvg-convert (librsvg) and ffmpeg on PATH. Uses a throwaway config dir
and priority file, so your real ~/.config/ewstui and todo.txt are untouched.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent.parent
# Frames, throwaway config and priority file (build/ is git-ignored). A
# fresh dir each run, so nothing from a previous recording leaks in.
(REPO / "build").mkdir(exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="demo-gif-", dir=REPO / "build"))
os.environ["XDG_CONFIG_HOME"] = str(WORK / "config")  # before importing ewstui: never read the real config

from ewstui import app as app_module  # noqa: E402
from ewstui.app import EwstuiApp  # noqa: E402
from ewstui.config import config_from_args  # noqa: E402
from ewstui.demo_backend import DEMO_ROOMS, DemoCalendarClient, DemoMailClient  # noqa: E402

SIZE = (160, 42)  # terminal columns x rows (wide enough for the room grid)
WIDTH_PX = 1440  # output width; height follows the screenshot's aspect ratio
CAPTION_PX = 64  # caption bar added below each screenshot (in SVG units)


def add_caption(svg: str, caption: str) -> str:
    """Grow the screenshot's canvas and write the caption in a bar below it.
    (Textual's notification toasts don't show up in exported screenshots.)"""
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    width, height = float(match[1]), float(match[2])
    svg = svg.replace(match[0], f'viewBox="0 0 {width} {height + CAPTION_PX}"', 1)
    bar = (
        f'<rect x="0" y="{height}" width="{width}" height="{CAPTION_PX}" fill="#1a1d23"/>'
        f'<text x="{width / 2}" y="{height + CAPTION_PX / 2 - 4}" text-anchor="middle" dominant-baseline="middle" '
        f'font-family="Helvetica, Arial, sans-serif" font-size="26" fill="#e5e7eb">{escape(caption)}</text>'
    )
    return svg.replace("</svg>", bar + "</svg>")


class Recorder:
    def __init__(self, app: EwstuiApp, pilot) -> None:
        self.app, self.pilot, self.frames = app, pilot, []

    async def settle(self, wait: float = 0.3) -> None:
        await self.pilot.pause(wait)
        await self.app.workers.wait_for_complete()
        await self.pilot.pause()

    async def keys(self, *keys: str, wait: float = 0.3) -> None:
        for key in keys:
            await self.pilot.press(key)
            await self.pilot.pause(0.05)
        await self.settle(wait)

    async def shot(self, caption: str, seconds: float = 2.2) -> None:
        """Screenshot with a caption bar below it."""
        self.app.clear_notifications()  # the app's own toasts ("Thread view on", …)
        await self.pilot.pause(0.3)
        path = WORK / f"frame{len(self.frames):02d}.svg"
        path.write_text(add_caption(self.app.export_screenshot(title="ewstui --demo"), caption))
        self.frames.append((path, seconds))


async def storyboard(theme_switch: bool = True) -> list[tuple[Path, float]]:
    app_module.PREFETCH_DELAY = 0.05
    cfg = config_from_args(["--demo", "--priority-file", str(WORK / "todo.txt"), "--layout", "stacked"])
    cfg.rooms = list(DEMO_ROOMS)
    app = EwstuiApp(DemoMailClient(), DemoCalendarClient(), cfg)
    async with app.run_test(size=SIZE) as pilot:
        r = Recorder(app, pilot)
        await r.settle()
        await r.shot("Mail: folders, message list and the email — all over EWS", 2.8)

        await r.keys("j", "j")
        await r.shot("j / k move — the reading pane follows (prefetched in the background)")
        await r.keys("o")
        await r.shot("o opens the email: j/k, Ctrl+d/u scroll it")

        await r.keys("U")
        await r.shot("U: every link in the email — Enter or 1-9 opens it in your browser", 2.8)
        await r.keys("escape", "h")

        await r.keys("t")
        await r.shot("t: threads — conversations as trees, newest reply on top, incl. your sent replies", 2.8)
        await r.keys("t", "g")

        await r.keys("m")
        await r.keys("a", "r", "c")
        await r.shot("m: move to a folder — recent folders first, fuzzy search as you type", 2.8)
        await r.keys("escape")

        await r.keys("V", "j", "j")
        await r.shot("V: select several emails, then archive / move / delete them all at once", 2.8)
        await r.keys("escape")

        await r.keys("g", "P", "j", "P", "j", "P")
        await r.keys("2")
        await r.keys("A", "j", "B")
        await r.shot("2: priority list (todo.txt) — A-Z sets priorities, Enter jumps to the email", 2.8)
        await r.keys("1")
        await r.shot("…and the P column shows them in the mail list")

        await r.keys("3")
        await r.shot("3: calendar")
        await r.keys("f")
        await r.keys("l", "l", "l", "l", "v", "j", "l", "l")
        await r.shot("f: find a free room — v selects rooms × times, Enter books them in one invite", 3.2)
        await r.keys("escape", "escape", "1")

        await r.keys("question_mark")
        await r.shot("?: help for the current view", 2.8)
        await r.keys("escape")

        if theme_switch:
            app.theme = "nord"
            await r.settle()
            await r.shot("Themes after tuxedo: Muted Slate (default) and Nord", 2.8)
        return r.frames


def to_gif(frames: list[tuple[Path, float]], out: Path) -> None:
    for tool in ("rsvg-convert", "ffmpeg"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} not found on PATH")
    pngs = []
    for svg, seconds in frames:
        png = svg.with_suffix(".png")
        subprocess.run(["rsvg-convert", "--width", str(WIDTH_PX), "--background-color", "#1a1d23",
                        "-o", str(png), str(svg)], check=True)
        pngs.append((png, seconds))
    concat = WORK / "frames.txt"
    lines = [f"file '{png}'\nduration {seconds}" for png, seconds in pngs]
    lines.append(f"file '{pngs[-1][0]}'")  # ffmpeg's concat demuxer ignores the last duration otherwise
    concat.write_text("\n".join(lines) + "\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    palette = WORK / "palette.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-vf", "palettegen=stats_mode=full", str(palette)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-i", str(palette), "-lavfi", "paletteuse=dither=none", "-loop", "0", str(out)], check=True)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "docs" / "demo.gif"
    frames = asyncio.run(storyboard())
    to_gif(frames, out)
    print(f"{len(frames)} frames -> {out} ({out.stat().st_size / 1e6:.1f} MB); frames kept in {WORK}")


if __name__ == "__main__":
    main()
