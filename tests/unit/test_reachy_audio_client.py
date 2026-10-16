"""Check microphone feedback prevention and USB playback cleanup without hardware."""

import asyncio
import base64
import importlib.util
import json
import math
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "reachy_audio_client", Path(__file__).resolve().parents[2] / "scripts" / "reachy_audio_client.py"
)
client = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(client)


def test_server_persona_selection_does_not_override_its_instructions():
    """A server-side enterprise prompt must keep its persona and tool allowlist."""
    session = client.build_session_config("enterprise_helpdesk")
    assert session["prompt"] == {"id": "enterprise_helpdesk"}
    assert "instructions" not in session
    assert session["nvidia"]["pipeline_mode"] == "generic-assistant"
    default = client.build_session_config()
    assert "Reachy" in default["instructions"]
    assert "prompt" not in default


@pytest.mark.parametrize("speaking", [False, True])
@pytest.mark.parametrize("interruptions", [False, True])
def test_capture_sends_silence_only_during_playback(monkeypatch, speaking, interruptions):
    """Suppress speaker feedback while preserving microphone audio between replies."""

    async def check():
        pcm = b"\x01\x02" * (client.CHUNK_BYTES // 2)
        recorder = Mock()
        recorder.stdout.readexactly = AsyncMock(side_effect=[pcm, asyncio.IncompleteReadError(b"", len(pcm))])
        monkeypatch.setattr(client.asyncio, "create_subprocess_exec", AsyncMock(return_value=recorder))
        cleanup = AsyncMock()
        monkeypatch.setattr(client, "stop_process", cleanup)
        audio = client.RobotAudio("test-device", allow_interruptions=interruptions)
        if speaking:
            audio.speaking.set()
        socket = Mock(send=AsyncMock())
        with pytest.raises(RuntimeError, match="Microphone capture stopped"):
            await audio.capture(socket)
        sent = json.loads(socket.send.call_args.args[0])
        assert base64.b64decode(sent["audio"]) == (bytes(len(pcm)) if speaking and not interruptions else pcm)
        cleanup.assert_awaited_once_with(recorder)

    asyncio.run(check())


def test_interruption_discards_buffered_audio_from_old_response(monkeypatch):
    """An interrupted response cannot keep playing or leak into the next turn."""

    async def check():
        audio = client.RobotAudio("test-device", allow_interruptions=True)
        monkeypatch.setattr(client, "stop_process", AsyncMock())

        async def events():
            yield json.dumps({"type": "response.created", "response": {"id": "old"}})
            yield json.dumps({"type": "response.output_audio.delta", "response_id": "old", "delta": "AQI="})
            audio.speaking.set()
            yield json.dumps({"type": "input_audio_buffer.speech_started"})
            yield json.dumps({"type": "response.output_audio.delta", "response_id": "old", "delta": "AwQ="})
            yield json.dumps({"type": "response.created", "response": {"id": "new"}})
            yield json.dumps({"type": "response.output_audio.delta", "response_id": "new", "delta": "BQY="})
            yield json.dumps({"type": "response.done", "response": {"id": "new", "status": "completed"}})

        with pytest.raises(RuntimeError, match="connection closed"):
            await audio.receive(events(), max_responses=0, show_transcripts=False)
        audio.play = AsyncMock()
        audio.finish_playback = AsyncMock()
        playback = asyncio.create_task(audio.play_queued_audio())
        await audio.queue.join()
        playback.cancel()
        await asyncio.gather(playback, return_exceptions=True)
        audio.play.assert_awaited_once_with(b"\x05\x06")
        assert audio.queued_bytes == 0

    asyncio.run(check())


def test_microphone_stays_muted_until_playback_drains():
    """A server response ending must not unmute before buffered sound finishes."""

    async def check():
        drained = asyncio.Event()
        waiting = asyncio.Event()

        async def wait():
            waiting.set()
            await drained.wait()
            return 0

        player = Mock(wait=AsyncMock(side_effect=wait))
        audio = client.RobotAudio("test-device")
        audio.player = player
        audio.speaking.set()
        task = asyncio.create_task(audio.finish_playback())
        await waiting.wait()
        assert audio.speaking.is_set()
        drained.set()
        await task
        assert not audio.speaking.is_set()
        player.stdin.close.assert_called_once()

    asyncio.run(check())


def test_cancelled_playback_reaps_the_detached_player(monkeypatch):
    """Stopping a client while audio drains must release the USB playback process."""

    async def check():
        waiting = asyncio.Event()

        async def wait():
            waiting.set()
            await asyncio.Event().wait()

        player = Mock(wait=AsyncMock(side_effect=wait))
        cleanup = AsyncMock()
        monkeypatch.setattr(client, "stop_process", cleanup)
        audio = client.RobotAudio("test-device")
        audio.player = player
        audio.speaking.set()
        task = asyncio.create_task(audio.finish_playback())
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cleanup.assert_awaited_once_with(player)
        assert not audio.speaking.is_set()

    asyncio.run(check())


@pytest.mark.parametrize(
    ("angle", "current_yaw", "target_yaw"),
    [(0, 0, 30), (math.pi, 0, -30), (math.radians(70), math.radians(10), 30)],
)
def test_head_turn_is_relative_bounded_and_preserves_other_joints(angle, current_yaw, target_yaw):
    """A measured direction changes only head yaw and respects the demo limit."""
    pose = {"x": 0.001, "y": 0.002, "z": 0.003, "roll": 0.04, "pitch": 0.05, "yaw": current_yaw}
    head = client.HeadFollower()
    head.request = Mock(
        side_effect=[{"angle": angle, "speech_detected": True}, pose, {"mode": "enabled"}, {"uuid": "move"}]
    )
    head.look()
    path, payload = head.request.call_args.args
    assert path == "move/goto"
    assert payload == {
        "head_pose": {**pose, "yaw": pytest.approx(math.radians(target_yaw))},
        "duration": 0.8,
        "interpolation": "minjerk",
    }


@pytest.mark.parametrize("angle", [math.nan, math.inf, -1, math.pi + 0.01, "invalid"])
def test_invalid_direction_never_moves_the_head(angle):
    """Reject sensor errors before requesting any robot pose or motion."""
    head = client.HeadFollower()
    head.request = Mock(return_value={"angle": angle, "speech_detected": True})
    with pytest.raises(ValueError, match="Invalid microphone direction"):
        head.look()
    head.request.assert_called_once_with("state/doa")


def test_head_ignores_silence_and_small_direction_changes():
    """Do not chase background noise or jitter when already facing the speaker."""
    head = client.HeadFollower()
    head.request = Mock(return_value={"angle": 0, "speech_detected": False})
    head.look()
    head.request.assert_called_once_with("state/doa")
    head.request = Mock(
        side_effect=[
            {"angle": math.pi / 2, "speech_detected": True},
            {key: 0 for key in ("x", "y", "z", "roll", "pitch", "yaw")},
        ]
    )
    head.look()
    assert head.request.call_count == 2


def test_sensor_error_does_not_stop_follower_and_events_are_throttled(monkeypatch):
    """A failed read is isolated, and rapid speech events cannot queue many moves."""

    async def check():
        head = client.HeadFollower()
        head.look = Mock(side_effect=OSError("sensor unavailable"))
        reads = asyncio.Queue()

        async def call(function):
            try:
                return function()
            finally:
                reads.put_nowait(True)

        monkeypatch.setattr(client.asyncio, "to_thread", call)
        monkeypatch.setattr(client.time, "monotonic", Mock(return_value=10))
        task = asyncio.create_task(head.follow())
        head.requested.set()
        await reads.get()
        head.requested.set()
        await asyncio.sleep(0)
        assert head.look.call_count == 1
        assert not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())
