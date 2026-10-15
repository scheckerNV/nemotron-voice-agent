# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Connect Reachy Mini USB audio to the blueprint's existing Realtime gateway.

Run on the Linux host with ALSA tools and websockets 15 or later. Microphone
input is replaced with silence during playback to prevent speaker feedback.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import logging
import math
import shutil
import signal
import ssl
import time
import urllib.request

from websockets.asyncio.client import connect

RATE = 16000
CHUNK_BYTES = RATE * 2 // 50  # 20 ms, mono signed 16-bit PCM.
LOGGER = logging.getLogger("reachy_audio")


class HeadFollower:
    """Aim the head on speech events using the local daemon, independently of audio."""

    def __init__(self) -> None:
        """Use the existing host daemon; never accept motor commands from the LLM."""
        self.requested = asyncio.Event()
        self.last_attempt = -math.inf

    @staticmethod
    def request(path: str, payload: dict | None = None):
        """Bound local HTTP calls so an unavailable daemon cannot stall voice I/O."""
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:8000/api/" + path,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=1) as response:
            return json.load(response)

    def look(self) -> None:
        """Read current direction and pose, then issue a bounded yaw-only move."""
        direction = self.request("state/doa")
        if not direction or direction.get("speech_detected") is not True:
            return
        angle = direction["angle"]
        if not isinstance(angle, (int, float)) or not math.isfinite(angle) or not 0 <= angle <= math.pi:
            raise ValueError("Invalid microphone direction")
        pose = self.request("state/present_head_pose")
        keys = ("x", "y", "z", "roll", "pitch", "yaw")
        if not isinstance(pose, dict) or any(
            not isinstance(pose.get(key), (int, float)) or not math.isfinite(pose[key]) for key in keys
        ):
            raise ValueError("Invalid current head pose")
        limit = math.radians(30)
        # The microphone array moves with the head; its direction is relative to it.
        yaw = max(-limit, min(limit, pose["yaw"] + math.pi / 2 - angle))
        if abs(yaw - pose["yaw"]) < math.radians(8):
            return
        if self.request("motors/status").get("mode") != "enabled":
            raise ValueError("Reachy motors are not enabled")
        target = {key: pose[key] for key in keys}
        target["yaw"] = yaw
        self.request("move/goto", {"head_pose": target, "duration": 0.8, "interpolation": "minjerk"})
        LOGGER.info("Turning toward speech: head yaw %.0f degrees", math.degrees(yaw))

    async def follow(self) -> None:
        """Coalesce speech events and isolate sensor failures from voice streaming."""
        while True:
            await self.requested.wait()
            self.requested.clear()
            now = time.monotonic()
            if now - self.last_attempt < 1.5:
                continue
            self.last_attempt = now
            try:
                await asyncio.to_thread(self.look)
            except Exception as exc:
                LOGGER.warning("Skipping head turn: %s", exc)


async def stop_process(process: asyncio.subprocess.Process | None) -> None:
    """Reap an audio subprocess, including after cancellation or device failure."""
    if process is None:
        return
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


class RobotAudio:
    """Stream ALSA audio without recording files, with optional speech-directed motion."""

    def __init__(self, device: str, *, allow_interruptions: bool = False, head: HeadFollower | None = None) -> None:
        """Use one USB device for capture and playback."""
        self.device = device
        self.allow_interruptions = allow_interruptions
        self.head = head
        self.speaking = asyncio.Event()
        self.recorder: asyncio.subprocess.Process | None = None
        self.player: asyncio.subprocess.Process | None = None
        self.queue: asyncio.Queue[tuple[int, bytes | None]] = asyncio.Queue()
        self.generation = 0
        self.queued_bytes = 0
        self.response_id: str | None = None
        self.cancelled = False

    async def capture(self, websocket) -> None:
        """Send microphone PCM, or silence while the robot speaker is active."""
        self.recorder = await asyncio.create_subprocess_exec(
            "arecord",
            "-q",
            "-D",
            self.device,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(RATE),
            "-c",
            "1",
            stdout=asyncio.subprocess.PIPE,
        )
        try:
            while True:
                try:
                    pcm = await self.recorder.stdout.readexactly(CHUNK_BYTES)
                except asyncio.IncompleteReadError as exc:
                    raise RuntimeError("Microphone capture stopped; check the USB device and other listeners") from exc
                if self.speaking.is_set() and not self.allow_interruptions:
                    pcm = bytes(len(pcm))
                await websocket.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm).decode("ascii"),
                        }
                    )
                )
        finally:
            await stop_process(self.recorder)
            self.recorder = None

    async def play(self, pcm: bytes) -> None:
        """Start playback on the first returned audio chunk and stream the rest."""
        if self.player is None:
            self.speaking.set()
            self.player = await asyncio.create_subprocess_exec(
                "aplay",
                "-q",
                "-D",
                self.device,
                "-t",
                "raw",
                "-f",
                "S16_LE",
                "-r",
                str(RATE),
                "-c",
                "1",
                "--buffer-time=80000",
                "--period-time=20000",
                stdin=asyncio.subprocess.PIPE,
            )
            mode = "interruptions enabled" if self.allow_interruptions else "microphone muted"
            LOGGER.info("Reachy is speaking (%s)", mode)
        self.player.stdin.write(pcm)
        await self.player.stdin.drain()

    async def finish_playback(self, *, cancelled: bool = False) -> None:
        """Drain the speaker before listening again; discard cancelled audio."""
        player = self.player
        try:
            if player is not None:
                if cancelled:
                    await stop_process(player)
                else:
                    player.stdin.close()
                    try:
                        code = await asyncio.wait_for(player.wait(), timeout=90)
                    except BaseException:
                        await stop_process(player)
                        raise
                    if code:
                        raise RuntimeError(f"Speaker playback failed (aplay exit {code})")
                # Keep the capture stream moving while the acoustic tail decays.
                await asyncio.sleep(0.25)
        finally:
            if self.player is player:
                self.player = None
                self.speaking.clear()
        LOGGER.info("Listening through Reachy's microphone")

    async def abort_playback(self) -> None:
        """Invalidate queued speech and release the speaker immediately."""
        self.generation += 1
        player = self.player
        await stop_process(player)
        if self.player is player:
            self.player = None
            self.speaking.clear()

    async def play_queued_audio(self) -> None:
        """Keep WebSocket reception responsive while ALSA drains audio."""
        while True:
            generation, pcm = await self.queue.get()
            if pcm is not None:
                self.queued_bytes -= len(pcm)
            try:
                if generation != self.generation:
                    continue
                try:
                    if pcm is None:
                        await self.finish_playback()
                    else:
                        await self.play(pcm)
                        if generation != self.generation:
                            await self.finish_playback(cancelled=True)
                except (BrokenPipeError, ConnectionResetError, RuntimeError):
                    if generation == self.generation:
                        raise
            finally:
                self.queue.task_done()

    async def receive(self, websocket, *, max_responses: int, show_transcripts: bool) -> None:
        """Play server audio and honor response boundaries and cancellations."""
        completed = 0
        async for raw in websocket:
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "response.created":
                await self.abort_playback()
                self.response_id = event.get("response", {}).get("id")
                self.cancelled = False
            elif kind == "input_audio_buffer.speech_started":
                if self.head is not None:
                    self.head.requested.set()
                if self.allow_interruptions and self.speaking.is_set():
                    self.cancelled = True
                    await self.abort_playback()
                    LOGGER.info("Interrupted Reachy's reply")
            elif kind in {"response.output_audio.delta", "response.audio.delta"}:
                if self.cancelled or event.get("response_id", self.response_id) != self.response_id:
                    continue
                pcm = base64.b64decode(event["delta"], validate=True)
                self.queued_bytes += len(pcm)
                if self.queued_bytes > 8 * 1024 * 1024:
                    raise RuntimeError("Server audio exceeds the playback buffer")
                self.queue.put_nowait((self.generation, pcm))
            elif kind == "response.done":
                if event.get("response", {}).get("id", self.response_id) != self.response_id:
                    continue
                status = event.get("response", {}).get("status")
                if status in {"cancelled", "failed"}:
                    self.cancelled = True
                    await self.abort_playback()
                elif not self.cancelled:
                    self.queue.put_nowait((self.generation, None))
                if status == "failed":
                    raise RuntimeError("The voice-agent response failed; check the server logs")
                completed += 1
                if max_responses and completed >= max_responses:
                    await self.queue.join()
                    return
            elif kind == "error":
                raise RuntimeError(f"Voice agent: {event.get('error', {}).get('message', event)}")
            elif show_transcripts and kind in {
                "conversation.item.input_audio_transcription.completed",
                "response.output_audio_transcript.done",
                "response.audio_transcript.done",
            }:
                LOGGER.info("%s: %s", kind, event.get("transcript", ""))
        raise RuntimeError("The voice-agent connection closed")

    async def close(self) -> None:
        """Release USB audio on normal exit, interruption, and network errors."""
        await stop_process(self.recorder)
        await stop_process(self.player)
        self.recorder = self.player = None


async def run(args: argparse.Namespace) -> None:
    """Configure the existing gateway, then stream robot audio both ways."""
    task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, task.cancel)
    for command in ("arecord", "aplay"):
        if shutil.which(command) is None:
            raise RuntimeError(f"Missing {command}; install alsa-utils on the host")
    tls = None
    if args.url.startswith("wss://"):
        tls = ssl.create_default_context()
        if args.insecure:
            tls.check_hostname = False
            tls.verify_mode = ssl.CERT_NONE
    head = HeadFollower() if args.look_at_speaker else None
    audio = RobotAudio(args.device, allow_interruptions=args.allow_interruptions, head=head)
    try:
        async with connect(args.url, ssl=tls, proxy=None, max_size=4 * 1024 * 1024) as websocket:
            created = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if created.get("type") != "session.created":
                raise RuntimeError(f"Expected session.created, received {created.get('type')}")
            await websocket.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "instructions": "You are Reachy, a friendly voice assistant. Reply in one short sentence.",
                            "voice": "John",
                            "max_output_tokens": 128,
                            "audio": {
                                "input": {
                                    "format": {"type": "audio/pcm", "rate": RATE},
                                    "turn_detection": {"type": "server_vad"},
                                },
                                "output": {"format": {"type": "audio/pcm", "rate": RATE}},
                            },
                            "nvidia": {"pipeline_mode": "generic-assistant"},
                        },
                    }
                )
            )
            updated = json.loads(await asyncio.wait_for(websocket.recv(), timeout=60))
            if updated.get("type") != "session.updated":
                raise RuntimeError(f"Session setup failed: {updated}")
            LOGGER.info("Connected to the Nemotron voice agent; Ctrl+C stops the audio client")
            capture = asyncio.create_task(audio.capture(websocket))
            playback = asyncio.create_task(audio.play_queued_audio())
            receive = asyncio.create_task(
                audio.receive(
                    websocket,
                    max_responses=args.max_responses,
                    show_transcripts=args.show_transcripts,
                )
            )
            tasks = {capture, receive, playback}
            if head is not None:
                tasks.add(asyncio.create_task(head.follow()))
            try:
                done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await audio.close()


def main() -> None:
    """Parse host audio options and run the connector."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="wss://127.0.0.1:7860/v1/realtime")
    parser.add_argument("--device", default="plughw:CARD=Audio,DEV=0")
    parser.add_argument(
        "--insecure", action="store_true", help="Accept the local demo server's self-signed certificate"
    )
    parser.add_argument(
        "--max-responses", type=int, default=0, help="Stop after this many responses (0: keep listening)"
    )
    parser.add_argument("--show-transcripts", action="store_true", help="Print transcripts to the terminal")
    parser.add_argument(
        "--allow-interruptions",
        action="store_true",
        help="Keep the mic active; requires working device echo cancellation",
    )
    parser.add_argument(
        "--look-at-speaker",
        action="store_true",
        help="Turn the head toward speech (up to 30 degrees); requires the local Reachy daemon and direction sensor",
    )
    args = parser.parse_args()
    if args.max_responses < 0:
        parser.error("--max-responses must be zero or greater")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        asyncio.run(run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        LOGGER.info("Stopped; robot audio released")
    except Exception as exc:
        parser.exit(1, f"Reachy audio client: {exc}\n")


if __name__ == "__main__":
    main()
