import os
import re
import logging
import subprocess
from typing import Dict, Any, List, Optional
import imageio_ffmpeg
from app.core.exceptions import ServiceUnavailableError, BadRequestError
from app.composer.ffmpeg_builder import RESOLUTION_MAP

logger = logging.getLogger("video_gen_app.composer")


def execute_composition(
    cmd: List[str],
    output_path: Optional[str] = None,
    timeout: float = 300.0,
) -> None:
    """Executes FFmpeg composition command securely without shell=True.
    
    Cleans up any partially written output file upon failure or timeout.
    """
    if not cmd or not isinstance(cmd, list):
        raise ValueError("Command must be a non-empty list of argument strings.")

    # Target output path for cleanup on failure
    target_out = output_path or cmd[-1]

    logger.info(f"Executing FFmpeg composition: {' '.join(str(c) for c in cmd[:4])}... -> {target_out}")

    try:
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )

        if process.returncode != 0:
            err_msg = (process.stderr or process.stdout or "").strip()
            # Clean up partial output file
            if os.path.exists(target_out):
                try:
                    os.remove(target_out)
                except Exception:
                    pass
            logger.error(f"FFmpeg composition failed (exit code {process.returncode}):\n{err_msg[-1000:]}")
            raise ServiceUnavailableError(
                detail=f"Video composition failed with exit code {process.returncode}: {err_msg[-300:]}"
            )

        logger.info(f"FFmpeg composition completed successfully: {target_out}")

    except subprocess.TimeoutExpired as exc:
        if os.path.exists(target_out):
            try:
                os.remove(target_out)
            except Exception:
                pass
        logger.error(f"FFmpeg composition timed out after {timeout} seconds.")
        raise ServiceUnavailableError(
            detail=f"Video composition timed out after {timeout} seconds."
        ) from exc

    except FileNotFoundError as exc:
        logger.error(f"FFmpeg executable not found: {exc}")
        raise ServiceUnavailableError(
            detail="FFmpeg binary not found or not executable on the system."
        ) from exc


def validate_video_file(
    file_path: str,
    expected_duration: Optional[float] = None,
    expected_ratio: Optional[str] = None,
    duration_tolerance: float = 1.0,
    min_file_size: int = 1024,
    ffmpeg_exe: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspects and validates a generated MP4 file with FFmpeg.
    
    Verifies container integrity, decoding, duration, resolution, and codecs.
    """
    if not os.path.isfile(file_path):
        raise BadRequestError(detail=f"Video file does not exist: {file_path}")

    file_size = os.path.getsize(file_path)
    if file_size < min_file_size:
        raise BadRequestError(
            detail=f"Video file is invalid or too small: {file_path} ({file_size} bytes)"
        )

    exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()

    # Run complete decode probe to ensure zero corruption
    probe_cmd = [exe, "-i", file_path, "-f", "null", "-"]
    try:
        res = subprocess.run(
            probe_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30.0,
            shell=False,
        )
    except Exception as exc:
        raise ServiceUnavailableError(detail=f"Failed to probe video file: {exc}") from exc

    if res.returncode != 0:
        err = (res.stderr or "").strip()[-400:]
        raise BadRequestError(detail=f"Video file verification failed decode test: {err}")

    output_log = res.stderr

    # 1. Parse duration
    duration: Optional[float] = None
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", output_log)
    if dur_match:
        h, m, s = dur_match.groups()
        duration = round(int(h) * 3600 + int(m) * 60 + float(s), 2)

    if duration is None or duration <= 0:
        raise BadRequestError(detail=f"Unable to determine duration of video: {file_path}")

    if expected_duration is not None:
        diff = abs(duration - expected_duration)
        if diff > duration_tolerance:
            raise BadRequestError(
                detail=f"Video duration {duration}s deviates from expected {expected_duration}s by {diff:.2f}s (tolerance: {duration_tolerance}s)"
            )

    # 2. Parse resolution
    width: Optional[int] = None
    height: Optional[int] = None
    res_match = re.search(r"Video:.*?,\s*(\d+)x(\d+)", output_log)
    if res_match:
        width = int(res_match.group(1))
        height = int(res_match.group(2))

    if expected_ratio and expected_ratio in RESOLUTION_MAP:
        exp_w, exp_h = RESOLUTION_MAP[expected_ratio]
        if width != exp_w or height != exp_h:
            raise BadRequestError(
                detail=f"Video resolution {width}x{height} does not match expected {exp_w}x{exp_h} for aspect ratio {expected_ratio}"
            )

    # 3. Check codecs
    has_h264 = "h264" in output_log.lower()
    has_aac = "aac" in output_log.lower()

    if not has_h264:
        raise BadRequestError(detail=f"Video does not contain expected H.264 video stream: {file_path}")
    if not has_aac:
        raise BadRequestError(detail=f"Video does not contain expected AAC audio stream: {file_path}")

    return {
        "file_path": file_path,
        "file_size": file_size,
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else None,
        "video_codec": "h264",
        "audio_codec": "aac",
    }
