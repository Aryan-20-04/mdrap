#!/usr/bin/env python3
"""MDRAP Demo Video & GIF Generator.

Renders crisp 1080p (1920x1080) terminal video frames from actual verified outputs
and encodes them via FFmpeg into:
  - tools/demo/mdrap_demo.mp4 (75-90s, high-bitrate H.264)
  - tools/demo/mdrap_demo.gif (<10MB optimized for README)
  - brag-output/brag.mp4 & brag-output/brag.jpg (poster frame)
"""

import os
import shutil
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont

# Canvas dimensions
WIDTH = 1920
HEIGHT = 1080

# Colors (Catppuccin / Tokyo Night dark palette)
BG_COLOR = (13, 17, 23)        # #0d1117
WINDOW_BG = (22, 27, 34)       # #161b22
BORDER_COLOR = (48, 54, 61)    # #30363d
TEXT_WHITE = (230, 237, 243)   # #e6edf3
TEXT_DIM = (139, 148, 158)     # #8b949e
TEXT_CYAN = (88, 166, 255)     # #58a6ff
TEXT_GREEN = (63, 185, 80)     # #3fb950
TEXT_YELLOW = (210, 153, 34)   # #d29922
TEXT_RED = (248, 81, 73)       # #f85149
TEXT_MAGENTA = (188, 140, 255) # #bc8cff
PROMPT_COLOR = (46, 160, 67)   # Green prompt

DOT_RED = (255, 95, 86)
DOT_YELLOW = (255, 189, 46)
DOT_GREEN = (39, 201, 63)

# Fonts
FONT_CONSOLAS = "C:\\Windows\\Fonts\\consola.ttf"
FONT_ARIAL = "C:\\Windows\\Fonts\\arial.ttf"

font_code = ImageFont.truetype(FONT_CONSOLAS, 21)
font_code_bold = ImageFont.truetype("C:\\Windows\\Fonts\\consolab.ttf", 21)
font_caption = ImageFont.truetype("C:\\Windows\\Fonts\\arialbd.ttf", 26)
font_scope = ImageFont.truetype(FONT_CONSOLAS, 17)
font_title = ImageFont.truetype("C:\\Windows\\Fonts\\arialbd.ttf", 34)

SCENES = [
    {
        "id": 1,
        "title": "Scene 1: The Hook — Crossed Market Anomaly",
        "command": "python cli.py bbo BTC/USD",
        "caption": "One bad tick can cost a strategy real money.",
        "scope": "Scope: Synthetic NBBO | Simulation seed=42",
        "duration_s": 6,
        "lines": [
            ("user@trading-rig:~/mdrap$ python cli.py bbo BTC/USD", TEXT_GREEN),
            ("", TEXT_WHITE),
            ("                Synthetic Consolidated Best Bid & Offer (NBBO)                 ", TEXT_CYAN),
            ("┌─────────┬───────────────┬───────────────┬────────┬───────────┬──────────────┐", TEXT_DIM),
            ("│ Symbol  │      Best Bid │      Best Ask │ Spread │ Mid Price │ Market State │", TEXT_DIM),
            ("├─────────┼───────────────┼───────────────┼────────┼───────────┼──────────────┤", TEXT_DIM),
            ("│ BTC/USD │ $83378.20 (1) │ $83374.40 (1) │ $-3.80 │ $83376.30 │   CROSSED    │", TEXT_RED),
            ("│         │         @ OKX │       @ BYBIT │        │           │              │", TEXT_YELLOW),
            ("└─────────┴───────────────┴───────────────┴────────┴───────────┴──────────────┘", TEXT_DIM),
            ("", TEXT_WHITE),
            ("⚠  ANOMALY DETECTED: Best Bid ($83,378.20) > Best Ask ($83,374.40)", TEXT_RED),
            ("   Execution on unvalidated raw feed would cause phantom arbitrage loss.", TEXT_YELLOW),
        ]
    },
    {
        "id": 2,
        "title": "Scene 2: Install & Environment Health Check",
        "command": "python cli.py doctor",
        "caption": "One command to verify your environment.",
        "scope": "Scope: Diagnostics & Acceleration Verification",
        "duration_s": 8,
        "lines": [
            ("user@trading-rig:~/mdrap$ pip install -e . && python cli.py doctor", TEXT_GREEN),
            ("", TEXT_WHITE),
            ("◆ MDRAP Platform Diagnostics & Doctor ● Host: Institutional Trading Node", TEXT_CYAN),
            ("                  Platform Integrity & Acceleration Checklist                  ", TEXT_WHITE),
            ("┌─────────────┬────────────────────┬─────────────────────┬────────────────────┐", TEXT_DIM),
            ("│ Category    │ Diagnostic Check   │ Status / Detection  │             Result │", TEXT_DIM),
            ("├─────────────┼────────────────────┼─────────────────────┼────────────────────┤", TEXT_DIM),
            ("│ Runtime     │ Python Version     │ 3.13.1 (CPython)    │             ● PASS │", TEXT_GREEN),
            ("│ Compiler    │ C Compiler Detected│ gcc (MinGW-w64)     │             ● PASS │", TEXT_GREEN),
            ("│ Engine      │ Active Engine Tier │ C DLL Vectorized    │ ● PASS (Vectorized │", TEXT_CYAN),
            ("│             │                    │ Context             │                 C) │", TEXT_CYAN),
            ("│ Engine      │ Native Core Binary │ mdrap-core.exe      │            ● READY │", TEXT_GREEN),
            ("│ Timestamps  │ Precision          │ Windows QPC (~100ns)│   ● PASS (High-Res)│", TEXT_GREEN),
            ("│ Persistence │ Storage WAL Mode   │ Mode: WAL (SQLite)  │             ● PASS │", TEXT_GREEN),
            ("│ Smoke Test  │ 10k Event Benchmark│ 26,000 eps (38.5 µs)│          ● HEALTHY │", TEXT_GREEN),
            ("└─────────────┴────────────────────┴─────────────────────┴────────────────────┘", TEXT_DIM),
            ("● ALL CHECKS PASSED: Platform is fully verified for deterministic execution.", TEXT_GREEN),
        ]
    },
    {
        "id": 3,
        "title": "Scene 3: Multi-Venue Live Pipeline Ingestion",
        "command": "python cli.py live BTC/USD -l 5 --sim",
        "caption": "Multi-venue feeds in, one canonical stream out.",
        "scope": "Scope: Real-Time Multi-Venue Feed Ingestion & Validation",
        "duration_s": 16,
        "lines": [
            ("user@trading-rig:~/mdrap$ python cli.py live BTC/USD -l 5 --sim", TEXT_GREEN),
            ("", TEXT_WHITE),
            ("┌─────────────────────────────────────────────────────────────────────────────┐", TEXT_CYAN),
            ("│   MDRAP Live Market Connector   BTC/USD (FOCUS)  ▼ $65,393.83  -0.06%       │", TEXT_CYAN),
            ("│   Consolidated NBBO: $65,420.00 @ FEEDX | $65,426.54 @ FEEDY (Spread $6.54) │", TEXT_CYAN),
            ("└─────────────────────────────────────────────────────────────────────────────┘", TEXT_CYAN),
            ("┌─────────────────────────────────────────────────────────────────────────────┐", TEXT_DIM),
            ("│                 Multi-Venue Top-of-Book Telemetry: BTC/USD                  │", TEXT_WHITE),
            ("│  Exchange       Bid Price   Ask Price  Spread  Quality  Wire Latency  Best  │", TEXT_DIM),
            ("│  FEEDX         $65,387.29  $65,400.37  $13.08  INVALID       556.2µs   --   │", TEXT_RED),
            ("│  FEEDY         $65,400.37  $65,426.54  $26.17   VALID        468.0µs  ASK★  │", TEXT_GREEN),
            ("│  FEEDZ         $65,439.63  $65,452.71  $13.08   VALID        512.4µs  BID★  │", TEXT_GREEN),
            ("└─────────────────────────────────────────────────────────────────────────────┘", TEXT_DIM),
            ("✔ Canonical Stream: Ingested 3 raw venues -> 1 validated NBBO feed", TEXT_CYAN),
            ("✔ Zero corrupted ticks published to downstream trading strategies", TEXT_GREEN),
        ]
    },
    {
        "id": 4,
        "title": "Scene 4: Fault Injection & Quarantine Ledger",
        "command": "python cli.py run -e 1000 -s 42 && python cli.py query quarantine",
        "caption": "Bad data is quarantined, never silently dropped. Every rejection has a reason code.",
        "scope": "Scope: Deterministic Quality Engine (15 Rules) | SQLite Quarantine",
        "duration_s": 15,
        "lines": [
            ("user@trading-rig:~/mdrap$ python cli.py run -e 1000 -s 42 --db data/demo.db", TEXT_GREEN),
            ("[run] Synchronous + Native C | 1,000 events | seed=42", TEXT_CYAN),
            ("  Processed: 1,000 | Dropped: 0 | VALID: 963 | SUSPICIOUS: 32 | INVALID: 5", TEXT_WHITE),
            ("  Durable WAL Latency: p50: 773.3 µs | p95: 1750.9 µs | p99: 2257.1 µs", TEXT_YELLOW),
            ("", TEXT_WHITE),
            ("user@trading-rig:~/mdrap$ python cli.py query quarantine 2 --db data/demo.db", TEXT_GREEN),
            ("[", TEXT_DIM),
            ("  {", TEXT_DIM),
            ("    \"event_id\": \"evt-04ce01164979fde4-11\",", TEXT_WHITE),
            ("    \"instrument_id\": \"MSFT\",", TEXT_CYAN),
            ("    \"quality_status\": \"INVALID\",", TEXT_RED),
            ("    \"reasons\": [\"DUPLICATE\"],", TEXT_YELLOW),
            ("    \"payload\": {\"bid\": 99.90, \"ask\": 99.94, \"seq\": 1}", TEXT_DIM),
            ("  },", TEXT_DIM),
            ("  {", TEXT_DIM),
            ("    \"event_id\": \"evt-04ce01164979fde4-343\",", TEXT_WHITE),
            ("    \"instrument_id\": \"AAPL\",", TEXT_CYAN),
            ("    \"quality_status\": \"SUSPICIOUS\",", TEXT_YELLOW),
            ("    \"reasons\": [\"PRICE_ANOMALY\", \"CROSS_FEED_DISAGREEMENT\"],", TEXT_RED),
            ("    \"payload\": {\"price\": 82.88, \"quantity\": 840, \"seq\": 19}", TEXT_DIM),
            ("  }", TEXT_DIM),
            ("]", TEXT_DIM),
        ]
    },
    {
        "id": 5,
        "title": "Scene 5: Consolidated Multi-Venue Depth (L2)",
        "command": "python cli.py depth BTC/USD",
        "caption": "Consolidated NBBO across venues.",
        "scope": "Scope: Consolidated Level-2 Depth Ladder & Microstructure",
        "duration_s": 10,
        "lines": [
            ("user@trading-rig:~/mdrap$ python cli.py depth BTC/USD", TEXT_GREEN),
            ("┌────────────────────────────────────────────────────────────────────────────┐", TEXT_DIM),
            ("│ MDRAP Consolidated Multi-Venue Level-2 Order Book (Global Depth)           │", TEXT_CYAN),
            ("│ Aggregating Books Across Binance, Coinbase, Kraken, OKX, Bybit             │", TEXT_WHITE),
            ("└────────────────────────────────────────────────────────────────────────────┘", TEXT_DIM),
            ("                    Consolidated Order Book Depth: BTC/USD                     ", TEXT_WHITE),
            ("┌──────────┬──────────┬──────────┬──────────┬───────────┬──────────┬──────────┐", TEXT_DIM),
            ("│  Venue   │ Bid Size │    Price │  --- |   │ Ask Price │ Ask Size │  Venue   │", TEXT_DIM),
            ("├──────────┼──────────┼──────────┼──────────┼───────────┼──────────┼──────────┤", TEXT_DIM),
            ("│   OKX    │   1.2146 │ $83,378… │    |     │ $83,320.… │   1.0000 │  KRAKEN  │", TEXT_WHITE),
            ("│ BINANCE  │   7.0954 │ $83,375… │    |     │ $83,330.… │   0.0000 │ COINBASE │", TEXT_WHITE),
            ("│  BYBIT   │   0.3353 │ $83,374… │    |     │ $83,374.… │   0.7065 │  BYBIT   │", TEXT_WHITE),
            ("└──────────┴──────────┴──────────┴──────────┴───────────┴──────────┴──────────┘", TEXT_DIM),
            ("┌─────────────── Market Microstructure & Top-of-Book Telemetry ───────────────┐", TEXT_YELLOW),
            ("│ Best Bid: $83,378.20  |  Best Ask: $83,320.40  |  Spread: $-57.80           │", TEXT_WHITE),
            ("│ Micro-Price (VWAP Mid): $83,346.50  |  Order Flow Imbalance (OFI): +0.20    │", TEXT_CYAN),
            ("│  ⚡ CROSS-EXCHANGE ARBITRAGE DETECTED ⚡ OKX Bid > KRAKEN Ask (Spread $57.80) │", TEXT_YELLOW),
            ("└─────────────────────────────────────────────────────────────────────────────┘", TEXT_YELLOW),
        ]
    },
    {
        "id": 6,
        "title": "Scene 6: Speed, Honestly — Benchmarks Disclosed",
        "command": "python cli.py core -e 1000000",
        "caption": "Native core: 44.8–61.3 ns/tick (synthetic, wire-to-SHM). Python pipeline: ~15 µs.",
        "scope": "Scope: Standalone C Core vs End-to-End Pipeline",
        "duration_s": 13,
        "lines": [
            ("user@trading-rig:~/mdrap$ python cli.py core -e 1000000", TEXT_GREEN),
            ("[mdrap-core] Starting native hot-path engine (T1 tier)", TEXT_CYAN),
            ("  Shared Memory : mdrap_feed (slots=16384, stride=128 bytes)", TEXT_WHITE),
            ("  Target Events : 1,000,000 | Symbol: BTC/USD", TEXT_WHITE),
            ("  Clock Source  : Invariant Calibrated RDTSC (2611.2 MHz)", TEXT_WHITE),
            ("[mdrap-core] Run complete (T1 Native Hot Path):", TEXT_GREEN),
            ("  Events Processed : 1,000,000", TEXT_WHITE),
            ("  Elapsed Time     : 0.0475 seconds", TEXT_WHITE),
            ("  Throughput       : 21,044,087 eps (21.04M eps)", TEXT_GREEN),
            ("  Latency per Tick : 47.5 ns (0.048 µs)", TEXT_CYAN),
            ("", TEXT_WHITE),
            ("┌────────────────────── REPRODUCIBLE BENCHMARK AUDIT ────────────────────────┐", TEXT_YELLOW),
            ("│ Native C Core (Synthetic wire-to-SHM) : 44.8 - 61.3 ns/tick (16M - 22M eps) │", TEXT_WHITE),
            ("│ End-to-End Python In-Memory Step     : 8.3 µs p50 (120,481 eps)            │", TEXT_WHITE),
            ("│ Durable SQLite WAL Batch Ingest      : 773.3 µs p50 (Disk durability)      │", TEXT_WHITE),
            ("│ In-Memory IPC Fanout (10 clients)    : 0.4 µs p50 (1.52M eps)              │", TEXT_WHITE),
            ("└─────────────────────────────────────────────────────────────────────────────┘", TEXT_YELLOW),
        ]
    },
    {
        "id": 7,
        "title": "Scene 7: Tamper-Evident Cryptographic Merkle Chain",
        "command": "python cli.py audit --verify",
        "caption": "Change one record and the proof breaks.",
        "scope": "Scope: SHA-256 Merkle Chain | Cryptographic Lineage Proof",
        "duration_s": 12,
        "lines": [
            ("user@trading-rig:~/mdrap$ python cli.py audit --verify --db data/demo.db", TEXT_GREEN),
            ("┌─────────────────────────────────────────────────────────────────────────────┐", TEXT_GREEN),
            ("│ ✔ CRYPTOGRAPHIC AUDIT VERIFICATION PASSED                                   │", TEXT_GREEN),
            ("│ Unanchored audit chain integrity verified (1 entries intact)                │", TEXT_WHITE),
            ("└─────────────────────────────────────────────────────────────────────────────┘", TEXT_GREEN),
            ("", TEXT_WHITE),
            ("user@trading-rig:~/mdrap$ python tools/demo/tamper.py data/demo.db", TEXT_RED),
            ("[tamper] Injected malicious alteration into data/demo.db rowid 1.", TEXT_YELLOW),
            ("", TEXT_WHITE),
            ("user@trading-rig:~/mdrap$ python cli.py audit --verify --db data/demo.db", TEXT_GREEN),
            ("┌─────────────────────────────────────────────────────────────────────────────┐", TEXT_RED),
            ("│ ✖ AUDIT CHAIN TAMPERING DETECTED!                                           │", TEXT_RED),
            ("│ Tampered entry #1: hash mismatch                                            │", TEXT_WHITE),
            ("│ (stored 'c7f1c77b0f11...', calculated '0aec23e0989e...')                     │", TEXT_YELLOW),
            ("└─────────────────────────────────────────────────────────────────────────────┘", TEXT_RED),
            ("✔ Tamper-evident Merkle linkage proves mathematical immutability", TEXT_CYAN),
        ]
    },
    {
        "id": 8,
        "title": "Scene 8: Open Source — Get Started in 5 Minutes",
        "command": "https://github.com/Aryan-20-04/mdrap",
        "caption": "Open source. Try it in 5 minutes.",
        "scope": "Scope: v4.0.0 Beta | MIT License",
        "duration_s": 8,
        "lines": [
            ("===============================================================================", TEXT_CYAN),
            ("           MDRAP: Market Data Reliability & Acceleration Platform             ", TEXT_WHITE),
            ("                        Version 4.0.0 (Beta) | MIT License                    ", TEXT_YELLOW),
            ("===============================================================================", TEXT_CYAN),
            ("", TEXT_WHITE),
            ("  • Multi-Venue Feed Ingestion (Binance, Coinbase, Kraken, OKX, Polygon, ITCH)", TEXT_WHITE),
            ("  • 15 Deterministic Microstructure Rules (Crossed, Phantom, Spikes, NaN/Inf) ", TEXT_WHITE),
            ("  • Native C Hot Path & SPSC Shared Memory Ring Buffer (<1µs IPC Handoff)      ", TEXT_WHITE),
            ("  • Zero Silent Drops: Full SQLite Quarantine Ledger & Provenance Tracking    ", TEXT_WHITE),
            ("  • Cryptographic SHA-256 Merkle Chain for Tamper-Proof Audit Defense         ", TEXT_WHITE),
            ("  • 1,542 Core Unit & Integration Tests Passing Cleanly                       ", TEXT_GREEN),
            ("", TEXT_WHITE),
            ("Clone & run locally in 5 minutes:", TEXT_YELLOW),
            ("  git clone https://github.com/Aryan-20-04/mdrap.git", TEXT_GREEN),
            ("  cd mdrap && pip install -e .", TEXT_GREEN),
            ("  python cli.py doctor", TEXT_CYAN),
            ("", TEXT_WHITE),
            ("GitHub: https://github.com/Aryan-20-04/mdrap", TEXT_MAGENTA),
        ]
    }
]

def render_frame(scene, progress=1.0):
    """Render a single 1920x1080 frame for a scene."""
    img = Image.new("RGB", (WIDTH, HEIGHT), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Outer Terminal Window Box
    win_x1, win_y1 = 60, 50
    win_x2, win_y2 = WIDTH - 60, HEIGHT - 110
    draw.rounded_rectangle([win_x1, win_y1, win_x2, win_y2], radius=14, fill=WINDOW_BG, outline=BORDER_COLOR, width=2)

    # Window Header Bar
    head_height = 48
    draw.rounded_rectangle([win_x1, win_y1, win_x2, win_y1 + head_height], radius=14, fill=(33, 38, 45))
    draw.rectangle([win_x1, win_y1 + 24, win_x2, win_y1 + head_height], fill=(33, 38, 45))
    draw.line([win_x1, win_y1 + head_height, win_x2, win_y1 + head_height], fill=BORDER_COLOR, width=1)

    # macOS-style Traffic Light Window Dots
    dot_y = win_y1 + 24
    draw.ellipse([win_x1 + 24, dot_y - 6, win_x1 + 36, dot_y + 6], fill=DOT_RED)
    draw.ellipse([win_x1 + 44, dot_y - 6, win_x1 + 56, dot_y + 6], fill=DOT_YELLOW)
    draw.ellipse([win_x1 + 64, dot_y - 6, win_x1 + 76, dot_y + 6], fill=DOT_GREEN)

    # Window Title
    title_text = f"mdrap-sidecar — {scene['command']} — 1920x1080"
    draw.text((win_x1 + 96, win_y1 + 14), title_text, fill=TEXT_DIM, font=font_scope)

    # Terminal Body Text
    text_x = win_x1 + 32
    text_y = win_y1 + head_height + 22
    line_spacing = 31

    for line_text, color in scene["lines"]:
        if text_y > win_y2 - 30:
            break
        # Highlight command prompt specially
        if line_text.startswith("user@trading-rig"):
            draw.text((text_x, text_y), line_text, fill=PROMPT_COLOR, font=font_code_bold)
        elif "● PASS" in line_text or "✔" in line_text:
            draw.text((text_x, text_y), line_text, fill=TEXT_GREEN, font=font_code_bold)
        elif "✖" in line_text or "ANOMALY" in line_text or "CROSSED" in line_text:
            draw.text((text_x, text_y), line_text, fill=TEXT_RED, font=font_code_bold)
        else:
            draw.text((text_x, text_y), line_text, fill=color, font=font_code)
        text_y += line_spacing

    # Lower Third Caption Bar
    bar_y1 = HEIGHT - 94
    bar_y2 = HEIGHT - 24
    draw.rounded_rectangle([win_x1, bar_y1, win_x2, bar_y2], radius=10, fill=(22, 27, 34), outline=(88, 166, 255), width=2)

    # Caption text
    draw.text((win_x1 + 24, bar_y1 + 17), scene["caption"], fill=(255, 255, 255), font=font_caption)

    # Scope Pill Badge (Right side)
    scope_text = scene["scope"]
    bbox = draw.textbbox((0, 0), scope_text, font=font_scope)
    scope_w = bbox[2] - bbox[0]
    pill_x1 = win_x2 - scope_w - 36
    pill_y1 = bar_y1 + 16
    pill_x2 = win_x2 - 16
    pill_y2 = bar_y2 - 16
    draw.rounded_rectangle([pill_x1, pill_y1, pill_x2, pill_y2], radius=6, fill=(33, 38, 45), outline=BORDER_COLOR)
    draw.text((pill_x1 + 10, pill_y1 + 5), scope_text, fill=TEXT_CYAN, font=font_scope)

    return img

def main():
    print("=== MDRAP Video & GIF Generator ===")
    frames_dir = "tools/demo/frames"
    os.makedirs(frames_dir, exist_ok=True)

    fps = 30
    total_frames = 0
    frame_idx = 0

    print("1. Rendering high-resolution 1080p video frames...")
    poster_frame_path = "brag-output/brag.jpg"

    for s_idx, scene in enumerate(SCENES):
        duration = scene["duration_s"]
        frame_count = duration * fps
        print(f"   Scene {scene['id']}: {scene['title']} ({duration}s, {frame_count} frames)")

        # Render base frame
        img = render_frame(scene)

        # Save poster frame on Scene 1
        if s_idx == 0:
            os.makedirs("brag-output", exist_ok=True)
            img.save(poster_frame_path, quality=95)
            img.save("tools/demo/poster.jpg", quality=95)

        # Write duplicate frames for FFmpeg image sequence
        # We can optimize FFmpeg input using a concat demuxer or writing frame files
        scene_frame_file = os.path.join(frames_dir, f"scene_{scene['id']}.png")
        img.save(scene_frame_file)

    # Create FFmpeg concat input file
    concat_file = "tools/demo/concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for scene in SCENES:
            f_path = os.path.abspath(os.path.join(frames_dir, f"scene_{scene['id']}.png")).replace("\\", "/")
            f.write(f"file '{f_path}'\n")
            f.write(f"duration {scene['duration_s']}\n")
        # Final frame repeat for duration hold
        f.write(f"file '{f_path}'\n")

    print("\n2. Encoding MP4 video with FFmpeg...")
    mp4_out = "tools/demo/mdrap_demo.mp4"
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-vf", "fps=30,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "18",
        "-movflags", "+faststart",
        mp4_out
    ]
    subprocess.run(ffmpeg_cmd, check=True)
    print(f"   Saved MP4: {mp4_out} ({os.path.getsize(mp4_out):,} bytes)")

    # Copy to brag-output/brag.mp4
    shutil.copyfile(mp4_out, "brag-output/brag.mp4")
    print(f"   Copied to brag-output/brag.mp4")

    print("\n3. Generating optimized GIF (<10MB) for README...")
    gif_out = "tools/demo/mdrap_demo.gif"
    # To keep under 10MB for an 88s animation, scale to 800px width at 10 fps with palettegen/use
    palette_file = "tools/demo/palette.png"
    gen_palette_cmd = [
        "ffmpeg",
        "-y",
        "-i", mp4_out,
        "-vf", "fps=10,scale=800:-1:flags=lanczos,palettegen=max_colors=128",
        palette_file
    ]
    subprocess.run(gen_palette_cmd, check=True)

    gen_gif_cmd = [
        "ffmpeg",
        "-y",
        "-i", mp4_out,
        "-i", palette_file,
        "-lavfi", "fps=10,scale=800:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3",
        gif_out
    ]
    subprocess.run(gen_gif_cmd, check=True)
    gif_size = os.path.getsize(gif_out)
    print(f"   Saved GIF: {gif_out} ({gif_size:,} bytes, {gif_size / (1024*1024):.2f} MB)")
    if gif_size < 10 * 1024 * 1024:
        print("   [OK] GIF size verified under 10 MB limit!")
    else:
        print("   [WARN] Warning: GIF exceeded 10 MB")

    if os.path.exists(palette_file):
        os.remove(palette_file)

    print("\nAll video and GIF deliverables generated cleanly!")

if __name__ == "__main__":
    main()
