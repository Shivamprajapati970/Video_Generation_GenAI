import os
import re
import time
import json
import wave
import math
import struct
import logging
import asyncio
import httpx
from typing import Optional, Dict, Any, List

from app.core.config import settings
from app.ai.base import BaseTTSProvider
from app.core.exceptions import AppError, ServiceUnavailableError, BadRequestError

logger = logging.getLogger("video_gen_app.chatterbox")


def split_text_into_chunks(text: str, max_chars: int = 280) -> List[str]:
    """Splits text into coherent sentence-based chunks of <= max_chars.
    
    Preserves all words and punctuation without omission or duplicate sentences.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Split by sentence boundaries (. , ! , ? , newline)
    sentences = re.split(r'(?<=[.!?\n])\s+', text)
    chunks = []
    current_chunk = ""

    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) > max_chars:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            words = s.split()
            sub_chunk = ""
            for w in words:
                if len(sub_chunk) + len(w) + 1 <= max_chars:
                    sub_chunk = f"{sub_chunk} {w}".strip()
                else:
                    if sub_chunk:
                        chunks.append(sub_chunk)
                    sub_chunk = w
            if sub_chunk:
                chunks.append(sub_chunk)
        elif len(current_chunk) + len(s) + 1 <= max_chars:
            current_chunk = f"{current_chunk} {s}".strip() if current_chunk else s
        else:
            chunks.append(current_chunk)
            current_chunk = s

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def concatenate_wav_files(input_paths: List[str], output_path: str) -> str:
    """Concatenates multiple PCM WAV files into a single continuous WAV file."""
    if not input_paths:
        raise BadRequestError(detail="No audio chunks provided for concatenation")
    if len(input_paths) == 1:
        import shutil
        if input_paths[0] != output_path:
            shutil.copy2(input_paths[0], output_path)
        return output_path

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with wave.open(input_paths[0], "rb") as first_wav:
        params = first_wav.getparams()

    with wave.open(output_path, "wb") as out_wav:
        out_wav.setparams(params)
        for path in input_paths:
            with wave.open(path, "rb") as in_wav:
                out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))

    return output_path


def verify_audio_content(file_path: str) -> Dict[str, Any]:
    """Verifies that the generated WAV file is valid, non-empty, and audible (not silent)."""
    if not file_path or not isinstance(file_path, (str, bytes, os.PathLike)):
        raise ServiceUnavailableError(detail=f"Audio file path is invalid: {file_path}")
    if not os.path.isfile(file_path):
        raise ServiceUnavailableError(detail=f"Audio file was not created: {file_path}")

    file_size = os.path.getsize(file_path)
    if file_size <= 44:
        raise ServiceUnavailableError(detail=f"Audio file is empty or corrupted: {file_size} bytes")

    try:
        with wave.open(file_path, "rb") as w:
            nframes = w.getnframes()
            sr = w.getframerate()
            channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            duration = nframes / float(sr) if sr > 0 else 0.0
            frames = w.readframes(min(nframes, sr * 5))  # inspect up to 5 seconds
    except Exception as exc:
        raise ServiceUnavailableError(detail=f"Invalid WAV audio container: {exc}") from exc

    if duration <= 0.1:
        raise ServiceUnavailableError(detail=f"Generated audio is unnaturally short: {duration:.2f}s")

    # Check for complete digital silence (all zero bytes)
    if frames == b"\x00" * len(frames):
        raise ServiceUnavailableError(detail="Generated audio file contains digital silence (all zero bytes)")

    # Check maximum amplitude across sample bytes
    max_amp = 0
    if sampwidth == 2:
        samples = [abs(int.from_bytes(frames[i:i+2], "little", signed=True)) for i in range(0, min(len(frames), 10000), 2)]
        max_amp = max(samples) if samples else 0
        if max_amp < 5:
            raise ServiceUnavailableError(detail=f"Generated audio amplitude is below threshold: {max_amp}")

    return {
        "duration_seconds": round(duration, 2),
        "sample_rate": sr,
        "channels": channels,
        "bits_per_sample": sampwidth * 8,
        "file_size": file_size,
        "max_amplitude": max_amp,
    }


class HFChatterboxProvider(BaseTTSProvider):
    """Voiceover narration synthesis using verified free public Hugging Face ZeroGPU Chatterbox Space.

    Model: Resemble AI Chatterbox TTS
    Space: ResembleAI/Chatterbox
    Hardware: Hugging Face ZeroGPU (zero-a10g)
    Gradio Version: 6.3.0
    """

    mock_mode: Optional[bool] = None

    split_text_into_chunks = staticmethod(split_text_into_chunks)
    concatenate_wav_files = staticmethod(concatenate_wav_files)
    verify_audio_content = staticmethod(verify_audio_content)

    def __init__(
        self,
        hf_token: Optional[str] = None,
        space_name: Optional[str] = None,
        mock_mode: Optional[bool] = None,
    ):
        self.hf_token = hf_token or settings.HF_TOKEN
        self.space_name = space_name or settings.HF_CHATTERBOX_SPACE or "ResembleAI/Chatterbox"
        self._mock_mode_param = mock_mode
        self.output_dir = os.path.join(settings.OUTPUT_DIR, "audio")
        os.makedirs(self.output_dir, exist_ok=True)

    @property
    def effective_mock_mode(self) -> bool:
        if self._mock_mode_param is not None:
            return self._mock_mode_param
        if HFChatterboxProvider.mock_mode is not None:
            return HFChatterboxProvider.mock_mode
        return settings.is_testing or not self.space_name

    def _get_space_host(self) -> str:
        """Derives standard Gradio host from space name."""
        clean = self.space_name.strip().rstrip("/")
        if clean.endswith(".hf.space"):
            return clean
        slug = clean.replace("/", "-").lower()
        return f"{slug}.hf.space"

    async def _synthesize_single_chunk(
        self,
        text: str,
        output_path: str,
        reference_audio_path: Optional[str] = None,
        seed: int = 42,
    ) -> str:
        """Synthesizes a single chunk (<= 300 characters) via Chatterbox Gradio 6 API."""
        space_host = self._get_space_host()
        call_url = f"https://{space_host}/gradio_api/call/generate_tts_audio"

        auth_attempts = []
        if self.hf_token and self.hf_token.strip():
            auth_attempts.append({"Authorization": f"Bearer {self.hf_token}"})
        # Always allow anonymous fallback for free public ZeroGPU pool
        auth_attempts.append({})

        payload = {
            "data": [
                text,
                None,   # Default natural reference voice
                0.5,    # Exaggeration (neutral = 0.5)
                0.8,    # Temperature
                int(seed),
                0.5,    # CFG/Pace
                False   # VAD trimming
            ]
        }

        last_error = None
        for attempt_idx, headers in enumerate(auth_attempts):
            mode_label = "authenticated" if headers else "anonymous"
            logger.info(f"Submitting Chatterbox TTS request ({mode_label}) for '{text[:40]}...'")

            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    # 1. Initiate job via POST
                    r_call = await client.post(call_url, json=payload, headers=headers)
                    if r_call.status_code != 200:
                        raise ServiceUnavailableError(
                            detail=f"Chatterbox API call rejected ({r_call.status_code}): {r_call.text[:200]}"
                        )

                    event_id = r_call.json().get("event_id")
                    if not event_id:
                        raise ServiceUnavailableError(detail="Chatterbox did not return a valid event_id")

                    # 2. Stream SSE events
                    stream_url = f"https://{space_host}/gradio_api/call/generate_tts_audio/{event_id}"
                    audio_url = None

                    async with client.stream("GET", stream_url, headers=headers, timeout=120.0) as sse_stream:
                        async for line in sse_stream.aiter_lines():
                            if not line:
                                continue
                            if line.startswith("event: error"):
                                logger.warning(f"Chatterbox SSE reported error event on attempt ({mode_label})")
                                raise ServiceUnavailableError(detail="ZeroGPU execution error / quota limit reached")
                            if line.startswith("data:"):
                                raw_data = line[5:].strip()
                                try:
                                    parsed = json.loads(raw_data)
                                    if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                                        audio_url = parsed[0].get("url")
                                        if audio_url:
                                            break
                                except Exception:
                                    pass

                    if not audio_url:
                        raise ServiceUnavailableError(detail="Chatterbox SSE completed without returning an audio URL")

                    # 3. Download generated audio file
                    logger.info(f"Downloading audio from Chatterbox: {audio_url}")
                    dl_resp = await client.get(audio_url, timeout=60.0)
                    if dl_resp.status_code != 200:
                        raise ServiceUnavailableError(
                            detail=f"Failed to download audio from Chatterbox ({dl_resp.status_code})"
                        )

                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(dl_resp.content)

                    return output_path

            except Exception as exc:
                last_error = exc
                if headers and attempt_idx < len(auth_attempts) - 1:
                    logger.warning(f"Chatterbox generation with token failed ({exc}); retrying anonymously...")
                    await asyncio.sleep(1)
                    continue
                logger.error(f"Chatterbox audio chunk generation failed: {exc}")
                raise ServiceUnavailableError(detail=f"Chatterbox generation failed: {exc}") from exc

        raise ServiceUnavailableError(detail=f"Chatterbox generation failed: {last_error}")

    async def generate_voiceover(
        self,
        narration_text: str,
        mood: str = "cinematic",
        target_duration: Optional[int] = None,
        reference_audio_path: Optional[str] = None,
        seed: int = 42,
    ) -> str:
        """Synthesizes high-quality voiceover narration audio.

        Handles text chunking for scripts > 300 characters, concatenates audio segments,
        and verifies non-empty, audible sound.
        """
        narration_text = (narration_text or "").strip()
        if not narration_text:
            raise BadRequestError(detail="Narration text cannot be empty for voiceover generation")

        timestamp = int(time.time())
        final_output_path = os.path.join(
            self.output_dir, f"voiceover_{timestamp}.wav"
        )

        if self.effective_mock_mode:
            logger.info("Generating mock voiceover audio (test mode)")
            dur = target_duration or max(3, int(len(narration_text) / 20))
            return self._generate_mock_audio(final_output_path, dur)

        # Split narration text if it exceeds 280 characters
        chunks = split_text_into_chunks(narration_text, max_chars=280)
        logger.info(f"Synthesizing voiceover in {len(chunks)} chunk(s) (total chars: {len(narration_text)})")

        chunk_paths = []
        try:
            for i, chunk in enumerate(chunks):
                chunk_file = os.path.join(
                    self.output_dir, f"chunk_{timestamp}_{i}.wav"
                )
                chunk_path = await self._synthesize_single_chunk(
                    text=chunk,
                    output_path=chunk_file,
                    reference_audio_path=reference_audio_path,
                    seed=seed + i,
                )
                chunk_paths.append(chunk_path)

            # Concatenate chunks into final WAV
            concatenate_wav_files(chunk_paths, final_output_path)

            # Verify that output audio is valid, non-empty, and audible
            meta = verify_audio_content(final_output_path)
            logger.info(
                f"Generated voiceover successfully: {final_output_path} "
                f"({meta['duration_seconds']}s, {meta['sample_rate']}Hz, {meta['file_size']} bytes)"
            )
            return final_output_path

        finally:
            # Clean up intermediate chunk files if multiple chunks were used
            if len(chunk_paths) > 1:
                for cp in chunk_paths:
                    if os.path.exists(cp) and cp != final_output_path:
                        try:
                            os.remove(cp)
                        except Exception:
                            pass

    async def generate_tts_audio(
        self,
        text: str,
        filename_prefix: str = "voiceover",
        reference_audio_path: Optional[str] = None,
        seed: int = 42,
    ) -> str:
        """Helper alias supporting text=... calling convention."""
        return await self.generate_voiceover(
            narration_text=text,
            reference_audio_path=reference_audio_path,
            seed=seed,
        )

    def _generate_mock_audio(self, output_path: str, duration: int = 5, sample_rate: int = 24000) -> str:
        """Generates a valid audible sine wave tone in PCM 16-bit WAV for fast local unit testing."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with wave.open(output_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            num_samples = int(duration * sample_rate)
            data = bytearray()
            for i in range(num_samples):
                # 440 Hz tone with audible amplitude
                sample = int(10000 * math.sin(2 * math.pi * 440 * (i / sample_rate)))
                data.extend(struct.pack("<h", sample))
            w.writeframes(data)
        return output_path
