#!/usr/bin/env python3
"""Pull a YouTube video transcript via yt-dlp.

Strategy:
  1. Try yt-dlp without cookies (some videos work)
  2. Fallback to yt-dlp --cookies-from-browser chrome (works around IP blocks
     by impersonating a logged-in browser session)
  3. Returns plain text transcript stitched from VTT subtitle file.

Usage:
  python yt_transcript.py <video_url_or_id>
  python yt_transcript.py <video_url_or_id> --no-cookies   # skip browser cookies
  python yt_transcript.py <video_url_or_id> --browser firefox  # use firefox cookies

Outputs plain text to stdout. Errors to stderr.
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def pull_transcript(video: str, browser: str | None = "chrome", lang: str = "en") -> str:
    """Download auto/manual subtitles via yt-dlp and return stitched plain text.

    Returns transcript text. Raises RuntimeError on failure.
    """
    # Normalize: accept either full URL or bare video ID
    if not video.startswith("http"):
        video = f"https://www.youtube.com/watch?v={video}"

    with tempfile.TemporaryDirectory() as tmp:
        out_template = str(Path(tmp) / "%(id)s.%(ext)s")
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--skip-download",
            "--write-auto-subs",
            "--write-subs",
            "--sub-langs", f"{lang}.*,en.*",
            "--sub-format", "vtt",
            "--output", out_template,
            video,
        ]
        if browser:
            cmd[3:3] = ["--cookies-from-browser", browser]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(
                f"yt-dlp failed (code {proc.returncode}):\nSTDERR:\n{proc.stderr[:1500]}"
            )

        # Find .vtt file produced
        vtt_files = list(Path(tmp).glob("*.vtt"))
        if not vtt_files:
            raise RuntimeError(
                f"No .vtt subtitle file produced. yt-dlp stdout:\n{proc.stdout[:800]}"
            )

        vtt = vtt_files[0]
        return _vtt_to_text(vtt.read_text(encoding="utf-8", errors="ignore"))


def _vtt_to_text(vtt: str) -> str:
    """Strip VTT headers / timestamps / tags, dedupe consecutive identical lines."""
    lines: list[str] = []
    for raw in vtt.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s == "WEBVTT" or s.startswith("NOTE") or s.startswith("Kind:") or s.startswith("Language:"):
            continue
        if "-->" in s:
            continue
        # Skip cue identifiers (numeric or hyphenated)
        if re.match(r"^\d+$", s) or re.match(r"^[\d\-:]+$", s):
            continue
        # Strip inline tags like <c>, <00:00:00.000>
        s = re.sub(r"<[^>]+>", "", s)
        if s and (not lines or lines[-1] != s):
            lines.append(s)
    return " ".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("video", help="YouTube URL or video ID")
    p.add_argument("--no-cookies", action="store_true", help="Skip --cookies-from-browser")
    p.add_argument("--browser", default="chrome", help="Browser for cookies (default: chrome)")
    p.add_argument("--lang", default="en", help="Preferred subtitle language (default: en)")
    args = p.parse_args()

    browser = None if args.no_cookies else args.browser
    try:
        text = pull_transcript(args.video, browser=browser, lang=args.lang)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
