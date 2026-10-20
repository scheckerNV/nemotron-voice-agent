# Use Reachy Mini for Voice Audio

Use Reachy Mini's USB microphone and speaker with an existing Nemotron Voice Agent deployment on the Thor. The [host client](../../scripts/reachy_audio_client.py) sends audio through the [Realtime Gateway](use-realtime-gateway.md) at `/v1/realtime`.

The path is Reachy microphone → host `arecord` → gateway → automatic speech recognition (ASR) → large language model (LLM) → text-to-speech (TTS) → host `aplay` → Reachy speaker. The gateway reuses your deployment's models; the client downloads no models and starts no inference service.

The client uses robot audio by default and can optionally turn its head toward detected speech. It does not use the camera or change OpenShell policies. By default, it uses the Generic Assistant without tools and supplies a short Reachy persona for the session.

To select Dash's catalog persona and tools, pass `--prompt-key car_assistant`. Refer to the [Car Destination-Finder Demo](use-car-assistant.md) for fictional destination lookup. The legacy [Nova example](use-enterprise-helpdesk.md) remains selectable with `--prompt-key enterprise_helpdesk`.

## Prepare the Thor

First, start and verify the [Thor deployment](../03-jetson-thor.md). Run the client on the Thor host where Reachy's USB audio device is attached, outside Docker. You need `uv`, the Advanced Linux Sound Architecture (ALSA) utilities `arecord` and `aplay`, and access to the USB audio device.

Check the devices from the host shell:

```bash
arecord -l
aplay -l
```

The default device is `plughw:CARD=Audio,DEV=0`. Input and output use 16 kHz, mono, signed 16-bit pulse-code modulation (PCM) audio. If your card name differs, pass the appropriate device with `--device`.

If the [reference Reachy project](https://github.com/marckarp/reachy-openshell-thor) already runs a microphone listener, stop that listener before starting this client. From that project's repository root, run:

```bash
robot/listen.sh stop
```

Keep the robot daemon and motor service running. Do not run two microphone listeners together. If the device remains busy, inspect its owner before stopping any leftover recording process.

## Test the Speaker

Run this command from the Nemotron Voice Agent repository root on the Thor:

```bash
uv run --no-project --with websockets==15.0.1 python scripts/reachy_audio_client.py --insecure --max-responses 1
```

The default URL is `wss://127.0.0.1:7860/v1/realtime`. `--insecure` disables certificate verification for this local, self-signed demonstration. Omit it when the endpoint has a trusted certificate. Use `--url` to select a different gateway endpoint.

With the deployment's welcome message enabled, expect one greeting through Reachy's speaker and then a clean exit. This checks output audio; it does not prove microphone recognition. If the welcome message is disabled, speak a short request to trigger the response.

## Talk Through Reachy

Start an interactive session from the same repository root:

```bash
uv run --no-project --with websockets==15.0.1 python scripts/reachy_audio_client.py --insecure
```

After the greeting finishes, say “Hello, what can you help me with?” Listen for the reply and then ask a follow-up.

The client listens to utterances while it runs, without a wake word. By default, it sends silence instead of microphone input during speaker playback and for 0.25 seconds afterward to limit feedback. This is half-duplex: wait until the robot finishes speaking before your next turn.

### Optional Interruptions

To keep the microphone active during replies, add `--allow-interruptions`:

```bash
uv run --no-project --with websockets==15.0.1 python scripts/reachy_audio_client.py --insecure --allow-interruptions
```

When the server detects new speech, the client stops playback and discards queued audio from the interrupted response. This mode requires working acoustic echo cancellation (AEC), which removes speaker output from microphone input. Otherwise, the robot can interrupt itself.

[Pollen's audio documentation](https://github.com/pollen-robotics/reachy_mini/blob/main/docs/source/platforms/reachy_mini/media_advanced_controls.md#microphones-and-speakers) describes device AEC as enabled by default. The client does not inspect or change those hardware settings; their actual state on the tested device was not verified. One physical lab test confirmed that speaking during a reply stopped playback and produced an answer to the new request. This does not establish echo cancellation reliability across devices or environments.

### Optional Head Turning

Add `--look-at-speaker` to turn toward detected speech. This requires the existing Reachy daemon at `http://127.0.0.1:8000`, enabled motors, and access to its sound-direction sensor:

```bash
uv run --no-project --with websockets==15.0.1 python scripts/reachy_audio_client.py --insecure --allow-interruptions --look-at-speaker
```

New speech triggers left/right head rotation, limited to ±30°, while preserving position and tilt. Turns take 0.8 seconds, ignore changes below 8°, and occur at most once every 1.5 seconds. Sound direction has front/back ambiguity, so this is approximate aiming. Unavailable readings skip movement; sensor errors log a warning while voice continues. Head movement is disabled unless you pass this flag. One physical lab test confirmed turning toward the speaker while replies and interruptions continued; other environments remain untested.

The daemon needs permission to read the USB sensor. Confirm the current USB device path before adjusting host permissions. Temporary permissions on a USB device node reset after reconnection; do not reuse a bus/device number from another host or session.

The client saves no recordings and hides transcripts by default. Pass `--show-transcripts` only when you want recognized and generated text printed in the terminal. The server retains its normal deployment logging behavior.

Basic microphone input and audible replies were confirmed on physical Reachy. Complete your own check with a greeting, a spoken request, a follow-up turn, and microphone release after stopping. Test interruption separately when using the optional mode.

## Stop and Recover

Press **Ctrl+C** to disconnect and close the recording and playback processes. The client also handles termination signals so stopping a background session releases USB audio. After it exits, you can restore the original listener with `robot/listen.sh start` from the reference project's root.

For common failures, check the following:

- **Device busy:** Confirm the original listener stopped. Inspect `fuser -v /dev/snd/*` and stop only the recording process that still owns this microphone.
- **No audio device:** Check the USB connection, robot power, and device names with `arecord -l` and `aplay -l`.
- **Connection refused or services unavailable:** Check the existing blueprint application and model services. Refer to [Thor troubleshooting](../06-troubleshooting.md#single-gpu).
- **Certificate error:** For this local self-signed demonstration, pass `--insecure`; use trusted certificates for other endpoints.
- **Repeated self-triggering:** Return to the default mode without `--allow-interruptions`, reduce speaker volume, and keep competing listeners stopped. The client does not provide software echo cancellation or configure device AEC.

For all CLI options, run the following command:

```bash
uv run --no-project --with websockets==15.0.1 python scripts/reachy_audio_client.py --help
```
