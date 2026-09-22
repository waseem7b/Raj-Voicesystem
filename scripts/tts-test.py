#!/usr/bin/env python3
"""TTS audio test harness for Voicsito.

Renders the canonical inbound script phrases against multiple ElevenLabs
voice-setting variants, writes MP3s to ``audio/test/<config>/<phrase>.mp3``,
and emits a side-by-side HTML comparison page at ``audio/test/index.html``.

Run with the project venv:
    .venv-extract/bin/python scripts/tts-test.py [config_name] [--force]

If ``config_name`` is omitted, all configs are rendered.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Windows legacy consoles default to cp1252 and crash on the ✓/✗ glyphs below.
# Force UTF-8 on stdout/stderr so progress printing never aborts the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / "config" / ".env"
OUT_ROOT = PROJECT_ROOT / "audio" / "test"

load_dotenv(ENV_PATH)

API_KEY = os.environ.get("ELEVENLABS_API_KEY")
# Live production voice, pulled from monolithic assistant 261bf1a0 on 2026-06-02
# (this is what push_squad.py clones onto all 3 stages). The voice ID drifted
# from the F2BOg7... value still recorded in docs/system-summary.md.
VOICE_ID = "UNCsznDbk5vRePab5lcd"
API_URL_TMPL = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    "?output_format=mp3_44100_128"
)
RATE_LIMIT_SLEEP = 0.5
MAX_WORKERS = 4

# ---------------------------------------------------------------------------
# Test corpus — the QUESTIONS the agent asks, copied verbatim from the live
# stage prompts (template vars filled with example values so the line is
# speakable). This sweep is about how natural the question delivery sounds at
# different speeds, so the corpus is question-weighted.
# ---------------------------------------------------------------------------
PHRASES: dict[str, str] = {
    # --- Stage 1 questions (INTEREST) ---
    "s1_ask_name": (
        "Yeah, appreciate you calling. I'm just driving right now so I might "
        "cut out here and there... so how can I help you?"
    ),
    "s1_services_q": (
        "Just for some clarity on your side, are you guys mostly doing epoxy, "
        "or do you also do concrete polishing or other types of flooring?"
    ),
    "s1_reveal_q": (
        "So I guess this conversation would be more or less around marketing "
        "— are you even open to having that conversation?"
    ),
    # --- Stage 2 discovery questions (QUALIFY) — the core of this test ---
    "s2_q1_permission": (
        "Do you mind if I ask you a couple quick questions about the business "
        "just to get a better feel?"
    ),
    "s2_q2_partners": (
        "It sounds like you're the boss man, is this solely your company, or "
        "do you have any partners?"
    ),
    "s2_q3_crew": "And I assume you have a crew, or is it just you?",
    "s2_q3_crew_size": "How many guys are on your crew overall?",
    "s2_q4_leadsource": (
        "Where's the majority of your business coming from right now? ... is "
        "it referrals and word of mouth, or anything online like Facebook or "
        "Google?"
    ),
    "s2_q5_volume": (
        "Roughly how many jobs are you doing on a monthly basis right now?"
    ),
    "s2_q6_capacity": (
        "If you took on more work, would you have to hire on, or could you "
        "handle more jobs with your current crew?"
    ),
    # --- Stage 3 questions (BOOK) ---
    "s3_ask_time": (
        "When's the next best time for us to speak? You a morning or evening "
        "person —"
    ),
    "s3_slot_offer": (
        "Cool — I've got Tuesday at ten in the morning or Wednesday at two in "
        "the afternoon, either of those work?"
    ),
    "s3_ask_email": (
        "What's a good email for you? I'll send over a calendar invite —"
    ),
}

# ---------------------------------------------------------------------------
# Configs to compare
# ---------------------------------------------------------------------------
# Every config mirrors the LIVE production voice EXACTLY; only `speed` varies,
# so the comparison isolates the one knob we're tuning. Production baseline
# (monolithic assistant 261bf1a0, pulled 2026-06-02): eleven_turbo_v2_5,
# stability 0.30, similarity 1.00, style 1.00, speaker_boost on, speed 1.00.
_PROD_VOICE_SETTINGS = {
    "stability": 0.30,
    "similarity_boost": 1.00,
    "style": 1.00,
    "use_speaker_boost": True,
    "speed": 1.0,
}


def _speed_config(speed: float) -> dict:
    return {
        "model": "eleven_turbo_v2_5",
        "voice_settings": {**_PROD_VOICE_SETTINGS, "speed": speed},
    }


CONFIGS: dict[str, dict] = {
    "speed_100": _speed_config(1.00),  # current production baseline
    "speed_095": _speed_config(0.95),
    "speed_092": _speed_config(0.92),
    "speed_090": _speed_config(0.90),
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def synth_one(
    client: httpx.Client,
    config_name: str,
    cfg: dict,
    phrase_key: str,
    text: str,
    force: bool,
) -> tuple[str, Path, str | None]:
    """Render a single phrase to MP3. Returns (label, path, error)."""
    out_dir = OUT_ROOT / config_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{phrase_key}.mp3"
    label = f"{config_name}/{phrase_key}.mp3"

    if out_path.exists() and not force:
        return label, out_path, "skip"

    body = {
        "text": text,
        "model_id": cfg["model"],
        "voice_settings": cfg["voice_settings"],
    }
    headers = {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    url = API_URL_TMPL.format(voice_id=VOICE_ID)

    try:
        resp = client.post(url, json=body, headers=headers, timeout=60.0)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        time.sleep(RATE_LIMIT_SLEEP)
        return label, out_path, None
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:200] if e.response is not None else ""
        return label, out_path, f"HTTP {e.response.status_code}: {detail}"
    except Exception as e:  # noqa: BLE001
        return label, out_path, f"{type(e).__name__}: {e}"


def render_configs(selected: list[str], force: bool) -> dict:
    """Render all (config, phrase) combos. Returns summary dict."""
    if not API_KEY:
        sys.exit("ERROR: ELEVENLABS_API_KEY not found in config/.env")

    tasks = []
    for cname in selected:
        cfg = CONFIGS[cname]
        for pkey, ptext in PHRASES.items():
            tasks.append((cname, cfg, pkey, ptext))

    total = len(tasks)
    summary = {"ok": 0, "skipped": 0, "errors": [], "bytes": 0}

    with httpx.Client() as client:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {
                ex.submit(synth_one, client, cn, cfg, pk, pt, force): (cn, pk)
                for cn, cfg, pk, pt in tasks
            }
            for i, fut in enumerate(as_completed(futures), 1):
                label, path, err = fut.result()
                if err is None:
                    size = path.stat().st_size
                    summary["ok"] += 1
                    summary["bytes"] += size
                    print(f"[{i}/{total}] {label} ✓ ({size:,} bytes)")
                elif err == "skip":
                    size = path.stat().st_size if path.exists() else 0
                    summary["skipped"] += 1
                    summary["bytes"] += size
                    print(f"[{i}/{total}] {label} (skipped, exists)")
                else:
                    summary["errors"].append((label, err))
                    print(f"[{i}/{total}] {label} ✗ {err}")

    return summary


# ---------------------------------------------------------------------------
# HTML index
# ---------------------------------------------------------------------------
def render_html(selected: list[str]) -> Path:
    """Write side-by-side comparison HTML to audio/test/index.html."""
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    out = OUT_ROOT / "index.html"

    # Build settings descriptor strings for the column header
    def cfg_blurb(name: str) -> str:
        c = CONFIGS[name]
        vs = c["voice_settings"]
        return (
            f"<code>{html.escape(c['model'])}</code><br>"
            f"stab {vs['stability']} · sim {vs['similarity_boost']} · "
            f"style {vs['style']} · speed {vs['speed']}"
        )

    rows_html: list[str] = []
    for pkey, ptext in PHRASES.items():
        cells = []
        for cname in selected:
            mp3_path = f"{cname}/{pkey}.mp3"
            full = OUT_ROOT / cname / f"{pkey}.mp3"
            if full.exists():
                cells.append(
                    f'<td><audio controls preload="none" src="{mp3_path}">'
                    f'</audio><br><a href="{mp3_path}" download>download</a></td>'
                )
            else:
                cells.append('<td class="missing">— not rendered —</td>')
        rows_html.append(
            f'<tr><th class="phrase"><div class="key">{html.escape(pkey)}</div>'
            f'<div class="text">{html.escape(ptext)}</div></th>'
            f"{''.join(cells)}</tr>"
        )

    header_cells = "".join(
        f'<th>{html.escape(c)}<br><span class="meta">{cfg_blurb(c)}</span></th>'
        for c in selected
    )

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Voicsito TTS test — {len(selected)} configs × {len(PHRASES)} phrases</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 24px; background: #fafafa; color: #111; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #111; color: #eee; }}
    th, td {{ border-color: #333 !important; }}
    .text {{ color: #aaa !important; }}
    .meta {{ color: #888 !important; }}
  }}
  h1 {{ margin: 0 0 4px; font-size: 20px; }}
  .sub {{ color: #666; font-size: 13px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 10px; vertical-align: top;
            text-align: left; }}
  th {{ background: rgba(0,0,0,0.04); font-size: 13px; }}
  th.phrase {{ width: 320px; }}
  .key {{ font-weight: 600; font-family: ui-monospace, monospace;
          font-size: 12px; }}
  .text {{ color: #555; font-size: 13px; margin-top: 4px; }}
  .meta {{ color: #666; font-weight: normal; font-size: 11px; }}
  audio {{ width: 240px; }}
  .missing {{ color: #999; font-size: 12px; font-style: italic; }}
  code {{ font-size: 11px; }}
  a {{ font-size: 11px; color: #06c; }}
</style>
</head>
<body>
<h1>Voicsito TTS test</h1>
<div class="sub">{len(selected)} configs × {len(PHRASES)} phrases · voice <code>{VOICE_ID}</code></div>
<table>
  <thead><tr><th class="phrase">Phrase</th>{header_cells}</tr></thead>
  <tbody>
    {''.join(rows_html)}
  </tbody>
</table>
</body>
</html>
"""
    out.write_text(doc, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Voicsito TTS test harness")
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help="Config name to render (default: all). Options: "
        + ", ".join(CONFIGS),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-render even if MP3 already exists",
    )
    args = parser.parse_args()

    if args.config is not None and args.config not in CONFIGS:
        sys.exit(
            f"ERROR: unknown config '{args.config}'. "
            f"Available: {', '.join(CONFIGS)}"
        )

    selected = [args.config] if args.config else list(CONFIGS.keys())

    print(
        f"Rendering {len(selected)} config(s) × {len(PHRASES)} phrases = "
        f"{len(selected) * len(PHRASES)} files"
    )
    print(f"Output: {OUT_ROOT}")
    print()

    summary = render_configs(selected, args.force)

    # HTML always reflects whatever exists on disk for ALL configs, so the
    # user can re-run individual configs and the index keeps growing.
    html_path = render_html(list(CONFIGS.keys()))

    print()
    print(
        f"Done. ok={summary['ok']} skipped={summary['skipped']} "
        f"errors={len(summary['errors'])} "
        f"total={summary['bytes'] / 1024:.1f} KB"
    )
    print(f"Open: {html_path}")
    if summary["errors"]:
        print("\nErrors:")
        for label, err in summary["errors"]:
            print(f"  {label}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
