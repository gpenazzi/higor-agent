"""Render the viewer to self-contained SVGs for the README.

Textual exports an SVG whose text colours live in a ``<style>`` block and whose font is
pulled from a CDN. GitHub strips both when it renders an image, which would leave the
screenshot unreadable, so the styles are inlined as presentation attributes and the font
becomes a local monospace stack. The result needs no network and survives sanitising.

A PNG is written alongside each SVG where macOS QuickLook is available, because an SVG
is still at the mercy of whatever sanitiser renders it. The README points at the PNGs.

Usage:
    uv run python scripts/screenshot.py [--state DIR] [--out DIR]

Whatever list is in the state directory's ``last_session.json`` is what gets drawn, so
point ``--state`` at a copy if you would rather not read from your own tree.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

FONT_STACK = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
    "'DejaVu Sans Mono', monospace"
)
SIZE = (118, 38)


def inline_styles(svg: str) -> str:
    """Move the ``<style>`` rules onto the elements, and drop the remote font."""
    match = re.search(r"<style>(.*?)</style>", svg, re.S)
    if match is None:
        return svg
    body = re.sub(r"@font-face\s*\{[^}]*\}", "", match.group(1))

    rules: dict[str, dict[str, str]] = {}
    for selector, block in re.findall(r"([^{}@]+)\{([^}]*)\}", body):
        name = selector.strip().lstrip(".")
        declarations = {}
        for item in block.split(";"):
            if ":" in item:
                prop, value = item.split(":", 1)
                declarations[prop.strip()] = value.strip()
        if name and declarations:
            rules[name] = declarations

    # SVG understands these as attributes, so they survive a stripped style block.
    as_attribute = ("fill", "font-weight", "font-style", "font-family", "font-size")

    def apply(element: re.Match[str]) -> str:
        tag, attrs = element.group(1), element.group(2)
        found = re.search(r'class="([^"]*)"', attrs)
        if found is None:
            return element.group(0)
        extra = ""
        for cls in found.group(1).split():
            for prop, value in rules.get(cls, {}).items():
                if prop not in as_attribute:
                    continue
                if re.search(rf'\b{re.escape(prop)}="', attrs):
                    continue
                if prop == "font-family":
                    value = FONT_STACK
                extra += f' {prop}="{value}"'
        return f"<{tag}{attrs}{extra}>"

    svg = re.sub(r"<(text|g|rect)((?:[^<>]|\n)*?)>", apply, svg)
    # Keep the block for renderers that honour it, minus the CDN @font-face rules.
    return svg.replace(match.group(1), body)


async def render(out_dir: Path) -> list[Path]:
    from higor_agent import store
    from higor_agent.view import ViewerApp

    # Screenshotting must never touch the reading log: it is real research history.
    store.log_interaction = lambda interaction: None

    written: list[Path] = []
    app = ViewerApp(follow=False)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        if not app.papers:
            raise SystemExit(
                "No published list in the state directory; nothing to screenshot."
            )
        shots = {"screenshot": None, "screenshot-detail": ("down", "down", "d")}
        for name, keys in shots.items():
            for key in keys or ():
                await pilot.press(key)
            await pilot.pause()
            path = out_dir / f"{name}.svg"
            path.write_text(inline_styles(app.export_screenshot(title="higor-agent")))
            written.append(path)
    return written


def rasterise(svg_path: Path, width: int = 1600) -> Path | None:
    """Render *svg_path* to a PNG beside it using QuickLook, trimming its square padding.

    Returns ``None`` where the macOS tools are missing; the SVG is still usable.
    """
    if not (shutil.which("qlmanage") and shutil.which("sips")):
        return None
    viewbox = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg_path.read_text())
    if viewbox is None:
        return None
    ratio = float(viewbox.group(2)) / float(viewbox.group(1))
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["qlmanage", "-t", "-s", str(width), "-o", tmp, str(svg_path)],
            check=True,
            capture_output=True,
        )
        produced = next(Path(tmp).glob("*.png"), None)
        if produced is None:
            return None
        png = svg_path.with_suffix(".png")
        # QuickLook returns a square thumbnail; crop away the letterboxing.
        subprocess.run(
            ["sips", "-c", str(round(width * ratio)), str(width), str(produced),
             "--out", str(png)],
            check=True,
            capture_output=True,
        )
    return png


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", help="state directory to read (default: $HIGOR_DIR or ~/.higor)")
    parser.add_argument("--out", default="docs", help="where to write the SVGs")
    args = parser.parse_args()
    if args.state:
        os.environ["HIGOR_DIR"] = args.state
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in asyncio.run(render(out_dir)):
        print(f"wrote {path}")
        png = rasterise(path)
        print(f"wrote {png}" if png else "  (no PNG: needs macOS qlmanage and sips)")


if __name__ == "__main__":
    main()
