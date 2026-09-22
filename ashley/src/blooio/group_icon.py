"""Render an Apple-style group icon: an emoji on a colored gradient circle.

Blooio's group-icon endpoint takes an image, so we reproduce what Apple's
"Change Name and Photo" picker produces. Rendering goes through headless
Chrome to get real Apple Color Emoji glyphs (PIL cannot render the color
bitmap strikes cleanly).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

# Gradients are sampled from the C7A logo (#6A64DC / #736CFD / #625AFF) and
# spread lighter-to-deeper on the diagonal so the circle reads with depth.
# All purples share the same deep bottom (#5A52E0); they differ at the top.
PRESETS = {
    "purple": ("#C7C3FF", "#8880FC", "#5A52E0"),      # default — strongest lift
    "purple-v1": ("#8B85FF", "#6C65FA", "#5A52E0"),   # first approved version
    "purple-light": ("#ADA8FF", "#7B74FB", "#5A52E0"),
    "purple-lightest": ("#DEDBFF", "#948DFD", "#5A52E0"),
    "yellow": ("#FFF0B8", "#FCE28C", "#F7D468"),
}
DEFAULT_GRADIENT = PRESETS["purple"]
SIZE = 1024

_CHROME_CANDIDATES = (
    Path.home()
    / "Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64"
    / "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
)

_HTML = """<style>
html,body{{margin:0;padding:0}}
.icon{{width:{size}px;height:{size}px;border-radius:50%;
  background:linear-gradient(160deg,{c0} 0%,{c1} 55%,{c2} 100%);
  display:flex;align-items:center;justify-content:center}}
.e{{font-family:"Apple Color Emoji";font-size:{font}px;line-height:1}}
</style>
<div class="icon"><span class="e">{emoji}</span></div>
"""


def _chrome() -> Path:
    for path in _CHROME_CANDIDATES:
        if path.exists():
            return path
    raise RuntimeError(
        "No Chrome binary found. Install Playwright's chromium or Google Chrome."
    )


def render_group_icon(
    emoji: str,
    out_path: str | Path,
    gradient: tuple[str, str, str] = DEFAULT_GRADIENT,
    size: int = SIZE,
) -> Path:
    """Write a square PNG of `emoji` centered on a gradient circle."""
    out = Path(out_path)
    html = _HTML.format(
        size=size,
        font=int(size * 0.547),
        c0=gradient[0],
        c1=gradient[1],
        c2=gradient[2],
        emoji=emoji,
    )
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "icon.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run(
            [
                str(_chrome()),
                "--headless",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--default-background-color=00000000",
                f"--window-size={size},{size}",
                f"--screenshot={out}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=90,
        )
    if not out.exists():
        raise RuntimeError(f"Chrome produced no screenshot at {out}")
    return out
