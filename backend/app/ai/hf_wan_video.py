import os
import time
import json
import base64
import logging
import asyncio
import httpx
from typing import Optional, Dict, Any
from app.core.config import settings
from app.ai.base import BaseVideoProvider
from app.core.exceptions import AppError, ServiceUnavailableError, BadRequestError

logger = logging.getLogger("video_gen_app.wan_video")


class HFWanVideoProvider(BaseVideoProvider):
    """Wan 2.2 Image-to-Video generation using verified free public Hugging Face Space.
    
    Model: Wan-AI/Wan2.2-I2V-A14B-Diffusers with Lightx2v Lightning LoRA.
    Hosted Space: zerogpu-aoti/wan2-2-fp8da-aoti-faster
    Hardware: Hugging Face ZeroGPU (zero-a10g)
    """

    mock_mode: Optional[bool] = None

    def __init__(
        self,
        hf_token: Optional[str] = None,
        space_name: Optional[str] = None,
        mock_mode: Optional[bool] = None,
    ):
        self.hf_token = hf_token or settings.HF_TOKEN
        self.space_name = space_name or settings.HF_WAN_SPACE or "zerogpu-aoti/wan2-2-fp8da-aoti-faster"
        self._mock_mode_param = mock_mode
        self.output_dir = os.path.join(settings.OUTPUT_DIR, "clips")
        os.makedirs(self.output_dir, exist_ok=True)
        self.last_run_metrics: Dict[int, Dict[str, Any]] = {}

    @property
    def effective_mock_mode(self) -> bool:
        if self._mock_mode_param is not None:
            return self._mock_mode_param
        if HFWanVideoProvider.mock_mode is not None:
            return HFWanVideoProvider.mock_mode
        return settings.is_testing or not self.space_name

    def _get_space_host(self) -> str:
        """Derive standard Gradio host from space name."""
        clean = self.space_name.strip().rstrip("/")
        if clean.endswith(".hf.space"):
            return clean
        # e.g. zerogpu-aoti/wan2-2-fp8da-aoti-faster -> zerogpu-aoti-wan2-2-fp8da-aoti-faster.hf.space
        slug = clean.replace("/", "-").lower()
        return f"{slug}.hf.space"

    async def generate_scene_clip(
        self,
        image_path: str,
        prompt: str,
        aspect_ratio: str = "9:16",
        duration_seconds: int = 5,
        scene_index: int = 1,
    ) -> str:
        """Submits image-to-video synthesis job to verified Wan 2.2 Hugging Face Space.

        Returns path to the generated clip MP4 file.
        """
        timestamp = int(time.time())
        output_clip_path = os.path.join(
            self.output_dir, f"scene_{scene_index}_{timestamp}.mp4"
        )

        if self.effective_mock_mode:
            logger.info(f"Generating simulated Wan clip for scene {scene_index} (mock mode)")
            return self._generate_simulated_clip(output_clip_path, aspect_ratio, duration_seconds)

        if not os.path.isfile(image_path):
            raise BadRequestError(detail=f"Reference image not found at path: {image_path}")

        space_host = self._get_space_host()
        headers = {}
        if self.hf_token and self.hf_token.strip():
            headers["Authorization"] = f"Bearer {self.hf_token.strip()}"

        # Clamp duration to 1.5s - 3.0s to stay comfortably within ZeroGPU free VRAM & compute lease
        clamped_duration = max(1.5, min(3.0, float(duration_seconds)))

        logger.info(
            f"Submitting Wan 2.2 clip generation for scene {scene_index} to {space_host} "
            f"(duration={clamped_duration}s, aspect_ratio={aspect_ratio})"
        )

        # Prepare reference photo as base64 data URI to guarantee delivery across all HuggingFace Space replicas
        try:
            with open(image_path, "rb") as f:
                file_bytes = f.read()
            ext = os.path.splitext(image_path)[1].lower()
            mime_type = "image/png" if ext == ".png" else "image/webp" if ext == ".webp" else "image/jpeg"
            b64_str = base64.b64encode(file_bytes).decode("utf-8")
            image_data_uri = f"data:{mime_type};base64,{b64_str}"
        except Exception as exc:
            logger.error(f"Failed to read image at {image_path}: {exc}")
            raise BadRequestError(detail=f"Failed to read reference image file: {exc}") from exc

        # Prepare header attempts: try user token first; if ZeroGPU quota is exhausted on the account, fallback to anonymous
        header_attempts = [headers]
        if "Authorization" in headers:
            header_attempts.append({})

        payload = {
            "data": [
                {"url": image_data_uri, "meta": {"_type": "gradio.FileData"}},
                prompt or "Cinematic motion, lifelike subtle movement, gentle atmosphere",
                4,  # Lightning LoRA inference steps (4-6 steps)
                "",  # Clear negative prompt for faster convergence and lower memory
                clamped_duration,
                1.0,  # Guidance scale 1
                1.0,  # Guidance scale 2
                42,   # Seed
                False  # Deterministic seed
            ]
        }

        call_url = f"https://{space_host}/gradio_api/call/generate_video"

        t_scene_start = time.time()
        queue_wait_duration: Optional[float] = None
        anonymous_used = False
        last_error = None

        for attempt_idx, current_headers in enumerate(header_attempts):
            has_fallback = (attempt_idx < len(header_attempts) - 1)
            is_auth_attempt = bool(current_headers.get("Authorization"))
            if attempt_idx > 0 or not is_auth_attempt:
                anonymous_used = True

            async with httpx.AsyncClient(timeout=180.0) as client:
                try:
                    call_resp = await client.post(call_url, json=payload, headers=current_headers)
                    if call_resp.status_code != 200:
                        err_msg = f"Wan 2.2 Space rejected request: {call_resp.status_code} {call_resp.text}"
                        if has_fallback:
                            logger.warning(f"{err_msg}. Retrying anonymously...")
                            continue
                        raise ServiceUnavailableError(detail=err_msg)

                    event_data = call_resp.json()
                    event_id = event_data.get("event_id")
                    if not event_id:
                        if has_fallback:
                            continue
                        raise ServiceUnavailableError(detail="Wan 2.2 Space did not return an event ID")

                    # Stream SSE events until completion or error
                    stream_url = f"https://{space_host}/gradio_api/call/generate_video/{event_id}"
                    remote_video_url = None
                    remote_video_path = None
                    stream_error = None
                    t_stream_connected = time.time()
                    t_first_event = None

                    async with client.stream("GET", stream_url, headers=current_headers, timeout=150.0) as sse_stream:
                        if sse_stream.status_code != 200:
                            if has_fallback:
                                continue
                            raise ServiceUnavailableError(
                                detail=f"Wan 2.2 SSE stream failed with status {sse_stream.status_code}"
                            )

                        current_event = None
                        async for line in sse_stream.aiter_lines():
                            if not line:
                                continue
                            logger.info(f"[Wan 2.2 SSE (attempt {attempt_idx})] {line}")
                            if t_first_event is None:
                                t_first_event = time.time()
                            if line.startswith("event:"):
                                current_event = line.replace("event:", "").strip()
                            elif line.startswith("data:") and current_event == "complete":
                                raw_data = line.replace("data:", "").strip()
                                parsed = json.loads(raw_data)
                                if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                                    remote_video_url = parsed[0].get("url")
                                    remote_video_path = parsed[0].get("path")
                                break
                            elif line.startswith("data:") and current_event == "error":
                                error_detail = line.replace("data:", "").strip()
                                if error_detail in ("null", "None", "", "{}"):
                                    stream_error = (
                                        "The Hugging Face Space returned an error "
                                        "(ZeroGPU quota exceeded on this token, GPU timeout, or queue limit)."
                                    )
                                else:
                                    stream_error = f"Remote error: {error_detail}"
                                break

                    if stream_error:
                        if has_fallback and is_auth_attempt:
                            logger.warning(
                                f"Wan 2.2 generation with HF token failed ({stream_error}). "
                                f"Retrying as anonymous request to bypass exhausted ZeroGPU user quota..."
                            )
                            continue
                        raise ServiceUnavailableError(detail=f"Wan 2.2 generation failed: {stream_error}")

                    if not remote_video_url and not remote_video_path:
                        if has_fallback:
                            continue
                        raise ServiceUnavailableError(detail="Wan 2.2 completed but no video path was returned")

                    # Download generated video file
                    if remote_video_url and remote_video_url.startswith("http"):
                        download_url = remote_video_url
                    else:
                        download_url = f"https://{space_host}/gradio_api/file={remote_video_path}"

                    dl_resp = await client.get(download_url, headers=current_headers)
                    if dl_resp.status_code != 200:
                        raise ServiceUnavailableError(
                            detail=f"Failed to download generated MP4 from Wan Space: {dl_resp.status_code}"
                        )
                    video_bytes = dl_resp.content

                    if len(video_bytes) < 5000 or b"ftyp" not in video_bytes[:100]:
                        raise ServiceUnavailableError(detail="Downloaded video file is corrupt or missing MP4 header")

                    with open(output_clip_path, "wb") as f:
                        f.write(video_bytes)

                    t_scene_end = time.time()
                    total_scene_time = round(t_scene_end - t_scene_start, 2)
                    queue_wait = round(t_first_event - t_stream_connected, 2) if t_first_event else None

                    self.last_run_metrics[scene_index] = {
                        "scene_index": scene_index,
                        "total_seconds": total_scene_time,
                        "queue_wait_seconds": queue_wait,
                        "gpu_processing_seconds": None,  # Space does not expose raw GPU compute time
                        "anonymous_fallback_used": anonymous_used,
                        "file_path": output_clip_path,
                        "file_size": len(video_bytes),
                    }

                    logger.info(
                        f"Successfully generated Wan 2.2 video clip for scene {scene_index}: "
                        f"{output_clip_path} ({len(video_bytes)} bytes) in {total_scene_time}s "
                        f"[queue_wait={queue_wait}s, anonymous_fallback={anonymous_used}]"
                    )
                    return output_clip_path

                except ServiceUnavailableError as sue:
                    last_error = sue
                    if has_fallback and is_auth_attempt:
                        logger.warning(f"Wan 2.2 attempt failed: {sue}. Falling back to anonymous request...")
                        continue
                    raise
                except Exception as exc:
                    last_error = exc
                    if has_fallback and is_auth_attempt:
                        logger.warning(f"Wan 2.2 connection error: {exc}. Falling back to anonymous request...")
                        continue
                    raise ServiceUnavailableError(detail=f"Wan 2.2 generation error: {exc}") from exc

            if last_error:
                raise ServiceUnavailableError(detail=f"Wan 2.2 generation failed on all attempts: {last_error}")

    def _generate_simulated_clip(self, output_path: str, aspect_ratio: str, duration: int) -> str:
        """Generates a test MP4 clip or writes a valid MP4 header."""
        dims = {"9:16": "720x1280", "16:9": "1280x720", "1:1": "720x720"}.get(
            aspect_ratio, "720x1280"
        )
        import subprocess
        try:
            cmd = [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"testsrc=size={dims}:rate=24:duration={duration}",
                "-pix_fmt", "yuv420p", output_path
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return output_path
        except Exception:
            # Minimal valid ISO BMFF MP4 header with ftyp box
            with open(output_path, "wb") as f:
                f.write(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41\x00\x00\x00\x08free")
            return output_path
