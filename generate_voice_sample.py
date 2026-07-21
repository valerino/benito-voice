#!/usr/bin/env python3
"""Generate an MP3 sample through the local Benito OmniVoice service."""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_URL = "http://localhost:8880/v1/audio/speech"
PROFILE_CHOICES = {
    1: "marziale_1",
    2: "marziale_2",
}
PROFILE_LEADING_TRIMS_MS = {
    "marziale_1": 1000,
    "marziale_2": 1100
}


def parse_args() -> argparse.Namespace:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(
        description="Generate an MP3 using an OmniVoice profile.",
        epilog="Profiles: 1=marziale_1 (default), 2=marziale_2",
    )
    parser.add_argument(
        "text",
        nargs="+",
        help="Text to speak; wrap it in quotes.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=f"voice/examples/sample-{timestamp}.mp3",
        help="Destination MP3 file (default: voice/examples/sample-DATE-TIME.mp3).",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("BENITO_VOICE_URL", DEFAULT_URL),
        help=f"TTS endpoint (default: BENITO_VOICE_URL or {DEFAULT_URL}).",
    )
    parser.add_argument(
        "--profile",
        type=int,
        choices=sorted(PROFILE_CHOICES),
        default=int(os.getenv("BENITO_VOICE_PROFILE", "1")),
        help=(
            "OmniVoice profile number (default: BENITO_VOICE_PROFILE or 1)."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Generation timeout in seconds (default: 600).",
    )
    parser.add_argument(
        "--leading-trim-ms",
        type=int,
        default=None,
        help=(
            "Milliseconds to remove from the beginning of the MP3. The default "
            "removes 1000 ms for marziale_1; use 0 to disable it."
        ),
    )
    parser.add_argument(
        "-p",
        "--play",
        action="store_true",
        help="Play the generated MP3 in the browser, without an external player.",
    )
    return parser.parse_args()


def trim_leading_audio(source: Path, output: Path, trim_ms: int) -> None:
    """Trim an MP3 with FFmpeg in the running OmniVoice container."""
    if trim_ms <= 0:
        source.replace(output)
        return

    token = uuid.uuid4().hex
    container_input = f"/tmp/omnivoice-input-{token}.mp3"
    container_output = f"/tmp/omnivoice-output-{token}.mp3"
    compose = ["docker", "compose"]
    try:
        subprocess.run(
            [*compose, "cp", str(source), f"omnivoice-tts:{container_input}"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                *compose,
                "exec",
                "-T",
                "omnivoice-tts",
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{trim_ms / 1000:.3f}",
                "-i",
                container_input,
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                container_output,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [*compose, "cp", f"omnivoice-tts:{container_output}", str(output)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise RuntimeError(
            "Unable to trim the audio prefix. Make sure "
            "`docker compose up -d` is running."
            + (f" Detail: {detail}" if detail else "")
        ) from exc
    finally:
        subprocess.run(
            [*compose, "exec", "-T", "omnivoice-tts", "rm", "-f", container_input, container_output],
            check=False,
            capture_output=True,
            text=True,
        )
        source.unlink(missing_ok=True)


def generate(
    text: str,
    output: Path,
    url: str,
    timeout: int,
    profile: str | None = None,
    leading_trim_ms: int = 0,
) -> tuple[int, str, str]:
    request_payload = {
        "model": "omnivoice",
        "input": text,
        "language": "it",
        "response_format": "mp3",
    }
    if profile:
        request_payload["voice"] = f"clone:{profile}"
    payload = json.dumps(
        request_payload,
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            audio = response.read()
            content_type = response.headers.get_content_type()
            engine = "omnivoice"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Voice service returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Voice service is unreachable at {url}. Start it with `docker compose up -d`."
        ) from exc

    if content_type not in {"audio/mpeg", "audio/mp3"}:
        raise RuntimeError(f"Unexpected format returned by the service: {content_type}")
    if len(audio) < 1024:
        raise RuntimeError("The service returned an empty or incomplete audio file.")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    temporary.write_bytes(audio)
    trim_leading_audio(temporary, output, leading_trim_ms)
    return output.stat().st_size, engine, profile or "default"


def play_audio(path: Path) -> bool:
    """Serve the MP3 locally and play it with the browser's audio support."""
    resolved = path.resolve()
    finished = threading.Event()
    audio = resolved.read_bytes()
    title = html.escape(resolved.name)
    page = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #101411; color: #f4f1e8; font: 16px system-ui, sans-serif; }}
  main {{ width: min(620px, 88vw); padding: 2rem; border: 1px solid #c9a45c;
          border-radius: 18px; background: #171c18; text-align: center; }}
  audio {{ width: 100%; margin-top: 1rem; }}
</style>
<main><h1>OmniVoice</h1><p>{title}</p>
<audio controls autoplay src="/audio" onended="fetch('/done', {{method:'POST'}})"></audio></main>
""".encode("utf-8")

    class PlayerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            if self.path == "/audio":
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(audio)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(audio)
                return
            if self.path in {"/", "/index.html"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(page)
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            if self.path == "/done":
                self.send_response(204)
                self.end_headers()
                finished.set()
                return
            self.send_error(404)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), PlayerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    player_url = f"http://127.0.0.1:{server.server_port}/"

    if not webbrowser.open(player_url, new=2):
        server.shutdown()
        server.server_close()
        print(
            "Warning: unable to open the browser; the MP3 was still created.",
            file=sys.stderr,
        )
        return False

    print("Player opened in the browser; press Ctrl+C to stop waiting.")
    try:
        while not finished.wait(0.25):
            pass
    except KeyboardInterrupt:
        print("Stopped waiting for the player.")
    finally:
        server.shutdown()
        server.server_close()
    return True


def main() -> int:
    args = parse_args()
    text = " ".join(args.text).strip()
    if not text:
        print("Error: text cannot be empty.", file=sys.stderr)
        return 2

    output = Path(args.output).expanduser()
    if output.suffix.lower() != ".mp3":
        output = output.with_suffix(".mp3")

    try:
        selected_profile = PROFILE_CHOICES.get(args.profile)
        leading_trim_ms = (
            args.leading_trim_ms
            if args.leading_trim_ms is not None
            else PROFILE_LEADING_TRIMS_MS.get(selected_profile, 0)
        )
        size, engine, profile = generate(
            text, output, args.url, args.timeout, selected_profile, leading_trim_ms
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Created: {output.resolve()}")
    print(f"Engine: {engine} | Profile: {profile} | Size: {size} bytes")
    if args.play and play_audio(output):
        print("Playback started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
