import os
import re
import time
import math
import json
import wave
import struct
import logging
import asyncio
import secrets
import httpx
from typing import Optional, Dict, Any, List

from app.core.config import settings
from app.ai.base import BaseMusicProvider
from app.core.exceptions import AppError, ServiceUnavailableError, BadRequestError

logger = logging.getLogger("video_gen_app.acestep")


def build_music_prompt(mood: str, custom_prompt: str = "") -> str:
    """Constructs expressive instrumental music tags from mood and user prompt."""
    mood_clean = (mood or "cinematic").strip()
    custom_clean = (custom_prompt or "").strip()

    base_tags = [mood_clean.lower()]
    if "acoustic" not in mood_clean.lower() and "piano" not in mood_clean.lower():
        base_tags.extend(["cinematic", "instrumental", "acoustic guitar", "gentle piano"])
    base_tags.extend(["uplifting", "warm", "85 BPM", "soundtrack"])

    if custom_clean:
        return f"{', '.join(base_tags)}, {custom_clean}".strip()
    return ", ".join(base_tags)


def verify_music_content(file_path: str) -> Dict[str, Any]:
    """Verifies that the generated audio file is valid, non-empty, and audible."""
    if not os.path.isfile(file_path):
        raise ServiceUnavailableError(detail=f"Music audio file was not created: {file_path}")

    file_size = os.path.getsize(file_path)
    if file_size <= 44:
        raise ServiceUnavailableError(detail=f"Music audio file is empty or corrupted: {file_size} bytes")

    duration = 0.0
    sample_rate = 44100
    channels = 2

    # Try reading as WAV container
    try:
        with wave.open(file_path, "rb") as w:
            nframes = w.getnframes()
            sample_rate = w.getframerate()
            channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            duration = nframes / float(sample_rate) if sample_rate > 0 else 0.0
            frames = w.readframes(min(nframes, sample_rate * 5))

            if frames == b"\x00" * len(frames):
                raise ServiceUnavailableError(detail="Generated music file contains digital silence (all zero bytes)")
    except wave.Error:
        # If not standard WAV (e.g., MP3 or container variant), estimate duration by file size
        duration = max(1.0, round(file_size / (32000 * 2), 2))

    if duration < 0.5:
        raise ServiceUnavailableError(detail=f"Generated music is unnaturally short: {duration:.2f}s")

    return {
        "duration_seconds": round(duration, 2),
        "sample_rate": sample_rate,
        "channels": channels,
        "file_size": file_size,
    }


class HFAceStepProvider(BaseMusicProvider):
    """Background music synthesis using verified free public ACE-Step Hugging Face Space.

    Model: ACE-Step 1.5 XL / ACE-Step Turbo
    Space: ACE-Step/ACE-Step
    Hardware: Hugging Face ZeroGPU (zero-a10g)
    Gradio Version: 5.27.0
    """

    mock_mode: Optional[bool] = None

    @staticmethod
    def build_music_prompt(mood: str, custom_prompt: str = "") -> str:
        """Constructs expressive instrumental music tags from mood and user prompt."""
        return build_music_prompt(mood, custom_prompt)

    @staticmethod
    def verify_music_content(file_path: str) -> Dict[str, Any]:
        """Verifies that the generated audio file is valid, non-empty, and audible."""
        return verify_music_content(file_path)

    def __init__(
        self,
        hf_token: Optional[str] = None,
        space_name: Optional[str] = None,
        mock_mode: Optional[bool] = None,
    ):
        self.hf_token = hf_token or settings.HF_TOKEN
        self.space_name = space_name or settings.HF_ACESTEP_SPACE or "ACE-Step/ACE-Step"
        self._mock_mode_param = mock_mode
        self.output_dir = os.path.join(settings.OUTPUT_DIR, "music")
        os.makedirs(self.output_dir, exist_ok=True)

    @property
    def effective_mock_mode(self) -> bool:
        if self._mock_mode_param is not None:
            return self._mock_mode_param
        if HFAceStepProvider.mock_mode is not None:
            return HFAceStepProvider.mock_mode
        return settings.is_testing or not self.space_name

    def _get_space_host(self) -> str:
        """Derives standard Gradio host from space name."""
        clean = self.space_name.strip().rstrip("/")
        if clean.endswith(".hf.space"):
            return clean
        # Handle canonical domain names like ACE-Step/Ace-Step-v1.5 -> ace-step-ace-step-v1-5.hf.space
        slug = clean.replace("/", "-").replace(".", "-").lower()
        return f"{slug}.hf.space"

    async def generate_music(
        self,
        mood: str,
        custom_prompt: str = "",
        duration_seconds: int = 15,
        seed: int = 42,
    ) -> str:
        """Generates mood-matched instrumental background music using ACE-Step.

        Returns path to the generated WAV or MP3 audio file.
        """
        mood_clean = (mood or "cinematic").strip()
        clamped_duration = max(5, min(60, int(duration_seconds or 15)))
        prompt_text = build_music_prompt(mood_clean, custom_prompt)

        timestamp = int(time.time())
        output_path = os.path.join(
            self.output_dir, f"music_{mood_clean.lower()}_{timestamp}.wav"
        )

        if self.effective_mock_mode:
            logger.info("Generating mock background music (test mode)")
            return self._generate_mock_audio(output_path, clamped_duration)

        space_host = self._get_space_host()
        join_url = f"https://{space_host}/gradio_api/queue/join"
        call_url = f"https://{space_host}/gradio_api/call/__call__"

        # Construct exact 22-parameter payload expected by ACE-Step/ACE-Step
        payload_data = [
            float(clamped_duration),   # 0: Audio Duration
            prompt_text,               # 1: Tags / prompt
            "",                        # 2: Lyrics (empty string for instrumental)
            30,                        # 3: Infer Steps (30 for fast generation)
            15.0,                      # 4: Guidance Scale
            "euler",                   # 5: Scheduler Type
            "apg",                     # 6: CFG Type
            10.0,                      # 7: Granularity Scale
            str(seed) if seed else "", # 8: manual seeds
            0.5,                       # 9: Guidance Interval
            0.0,                       # 10: Guidance Interval Decay
            3.0,                       # 11: Min Guidance Scale
            True,                      # 12: use ERG for tag
            False,                     # 13: use ERG for lyric
            True,                      # 14: use ERG for diffusion
            "",                        # 15: OSS Steps (must be empty string)
            0.0,                       # 16: Guidance Scale Text
            0.0,                       # 17: Guidance Scale Lyric
            False,                     # 18: Enable Audio2Audio
            0.5,                       # 19: Refer audio strength
            None,                      # 20: Reference Audio
            "none",                    # 21: Lora Name or Path
        ]

        auth_attempts = []
        if self.hf_token and self.hf_token.strip():
            auth_attempts.append({"Authorization": f"Bearer {self.hf_token.strip()}"})
        # Always allow anonymous fallback for public ZeroGPU pool
        auth_attempts.append({})

        last_error = None
        for attempt_idx, headers in enumerate(auth_attempts):
            mode_label = "authenticated" if headers else "anonymous"
            session_hash = secrets.token_hex(8)
            logger.info(
                f"Submitting ACE-Step music generation ({mode_label}) to {space_host} "
                f"(duration={clamped_duration}s, mood='{mood_clean}')"
            )

            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    # 1. Join queue
                    join_payload = {
                        "data": payload_data,
                        "fn_index": 11,
                        "session_hash": session_hash,
                    }
                    r_join = await client.post(join_url, json=join_payload, headers=headers)

                    if r_join.status_code != 200:
                        # Fallback to direct /call/__call__ if queue endpoint not responding
                        r_call = await client.post(call_url, json={"data": payload_data}, headers=headers)
                        if r_call.status_code != 200:
                            raise ServiceUnavailableError(
                                detail=f"ACE-Step Space rejected generation request ({r_call.status_code}): {r_call.text[:200]}"
                            )
                        event_id = r_call.json().get("event_id")
                        stream_url = f"https://{space_host}/gradio_api/call/__call__/{event_id}"
                    else:
                        stream_url = f"https://{space_host}/gradio_api/queue/data?session_hash={session_hash}"

                    # 2. Stream SSE events
                    audio_url = None
                    async with client.stream("GET", stream_url, headers=headers, timeout=240.0) as sse_stream:
                        async for line in sse_stream.aiter_lines():
                            if not line:
                                continue
                            if line.startswith("event: error"):
                                raise ServiceUnavailableError(detail="ZeroGPU execution error / quota limit reached")
                            if line.startswith("data:"):
                                raw_data = line[5:].strip()
                                try:
                                    parsed = json.loads(raw_data)
                                    # Handle queue protocol
                                    if parsed.get("msg") == "process_completed":
                                        output_obj = parsed.get("output", {})
                                        err = output_obj.get("error")
                                        if err:
                                            raise ServiceUnavailableError(detail=f"ACE-Step Space error: {err}")
                                        data_arr = output_obj.get("data", [])
                                        if data_arr and len(data_arr) > 0 and isinstance(data_arr[0], dict):
                                            audio_url = data_arr[0].get("url")
                                            if not audio_url and data_arr[0].get("path"):
                                                audio_url = f"https://{space_host}/gradio_api/file={data_arr[0]['path']}"
                                            break
                                    # Handle direct call protocol
                                    elif isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                                        audio_url = parsed[0].get("url")
                                        if not audio_url and parsed[0].get("path"):
                                            audio_url = f"https://{space_host}/gradio_api/file={parsed[0]['path']}"
                                        break
                                except Exception as exc:
                                    if isinstance(exc, ServiceUnavailableError):
                                        raise
                                    pass

                    if not audio_url:
                        raise ServiceUnavailableError(detail="ACE-Step Space completed without returning an audio URL")

                    # 3. Download generated audio file
                    dl_resp = await client.get(audio_url, headers=headers, timeout=60.0)
                    if dl_resp.status_code != 200 or len(dl_resp.content) < 100:
                        raise ServiceUnavailableError(
                            detail=f"Failed to download generated music from ACE-Step ({dl_resp.status_code})"
                        )

                    with open(output_path, "wb") as f:
                        f.write(dl_resp.content)

                    # 4. Verify audio integrity
                    meta = verify_music_content(output_path)
                    logger.info(
                        f"Generated background music successfully: {output_path} "
                        f"({meta['duration_seconds']}s, {meta['sample_rate']}Hz, {meta['file_size']} bytes)"
                    )
                    return output_path

            except Exception as exc:
                last_error = exc
                if headers and attempt_idx < len(auth_attempts) - 1:
                    logger.warning(f"ACE-Step generation with token failed ({exc}); retrying anonymously...")
                    await asyncio.sleep(1)
                    continue
                logger.error(f"ACE-Step music generation failed: {exc}")
                raise ServiceUnavailableError(detail=f"ACE-Step music generation failed: {exc}") from exc

        raise ServiceUnavailableError(detail=f"ACE-Step music generation failed: {last_error}")

    def _generate_mock_audio(
        self,
        output_path: str,
        duration: int = 15,
        sample_rate: int = 44100,
        duration_seconds: Optional[int] = None,
    ) -> str:
        """Generates a pleasant melodic synth progression in standard PCM 16-bit WAV for tests."""
        actual_duration = duration_seconds if duration_seconds is not None else duration
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        # Musical chord progression frequencies: C4 (261.63), E4 (329.63), G4 (392.00), B4 (493.88)
        notes = [261.63, 329.63, 392.00, 493.88, 392.00, 329.63]
        total_samples = int(actual_duration * sample_rate)
        samples_per_note = int(sample_rate * 0.5)

        with wave.open(output_path, "wb") as w:
            w.setnchannels(2)  # Stereo
            w.setsampwidth(2)   # 16-bit PCM
            w.setframerate(sample_rate)

            frames = bytearray()
            for i in range(total_samples):
                note_idx = (i // samples_per_note) % len(notes)
                freq = notes[note_idx]

                # Gentle envelope
                note_phase = (i % samples_per_note) / float(samples_per_note)
                envelope = math.sin(math.pi * note_phase) * 0.25

                # Stereo warmth: slightly detuned left/right channels
                val_left = int(32767 * envelope * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
                val_right = int(32767 * envelope * math.sin(2.0 * math.pi * (freq * 1.002) * (i / sample_rate)))

                frames.extend(struct.pack("<hh", val_left, val_right))

            w.writeframes(frames)

        return output_path
