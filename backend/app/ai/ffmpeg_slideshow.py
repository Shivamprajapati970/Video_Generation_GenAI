import os
import time
import logging
import asyncio
import subprocess
from typing import Optional, Dict, Any, Tuple
import imageio_ffmpeg

from app.core.config import settings
from app.ai.base import BaseVideoProvider
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.composer.ffmpeg_builder import get_target_resolution

logger = logging.getLogger("video_gen_app.ffmpeg_slideshow")

SUPPORTED_MOVEMENTS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "tilt_up", "tilt_down"]


class FFmpegSlideshowProvider(BaseVideoProvider):
    """High-fidelity animated photo slideshow engine using local FFmpeg.
    
    Transforms uploaded reference photographs into cinematic animated video scenes
    featuring smooth sub-pixel camera motions (Ken Burns zoom-in, zoom-out, pan, tilt)
    and crossfade transitions. Serves as both the primary engine for 'animated_still' scenes
    and the reliable zero-wait fallback when Wan 2.2 AI video generation is unavailable.
    """

    def __init__(
        self,
        output_dir: Optional[str] = None,
        ffmpeg_exe: Optional[str] = None,
    ):
        self.output_dir = output_dir or os.path.join(settings.OUTPUT_DIR, "clips")
        os.makedirs(self.output_dir, exist_ok=True)
        self.ffmpeg_exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()
        self.provider_name = "ffmpeg_slideshow"
        self.last_run_metrics: Dict[int, Dict[str, Any]] = {}

    @staticmethod
    def resolve_camera_movement(movement_str: Optional[str], scene_index: int = 1) -> str:
        """Parses scene camera description into a standardized movement type.
        
        If not specified or unrecognized, deterministically cycles through movements
        based on scene_index to create natural, varied visual rhythm.
        """
        if movement_str:
            clean = movement_str.lower().strip()
            if any(k in clean for k in ["pull-back", "pull back", "zoom-out", "zoom out", "receding", "establishing"]):
                return "zoom_out"
            if any(k in clean for k in ["tilt up", "crane up", "pedestal up", "upward"]):
                return "tilt_up"
            if any(k in clean for k in ["tilt down", "crane down", "downward"]):
                return "tilt_down"
            if any(k in clean for k in ["pan left", "tracking left", "sweep left", "lateral left"]):
                return "pan_left"
            if any(k in clean for k in ["pan right", "tracking right", "sweep right", "lateral right"]):
                return "pan_right"
            if any(k in clean for k in ["push-in", "push in", "zoom-in", "zoom in", "close-up", "closer"]):
                return "zoom_in"

        # Deterministic sequence for variety: zoom_in -> pan_left -> zoom_out -> pan_right -> tilt_up
        cycle = ["zoom_in", "pan_left", "zoom_out", "pan_right", "tilt_up"]
        return cycle[(scene_index - 1) % len(cycle)]

    def build_filtergraph(
        self,
        movement: str,
        duration_seconds: float,
        target_width: int,
        target_height: int,
        fps: int = 30,
        apply_fade: bool = True,
    ) -> str:
        """Builds FFmpeg filtergraph for distortion-free, smooth Ken Burns animation.
        
        Key principles:
        1. Pre-scales and crops input image to 2x target aspect ratio before zoompan.
           This strictly prevents face/aspect distortion while keeping 30fps motion razor-sharp.
        2. Applies mathematical sub-pixel camera trajectories (zoom, pan, tilt).
        3. Enforces target dimensions, SAR 1:1, DAR, yuv420p pixel format.
        4. Applies gentle 0.3s start/end fade dips for seamless transitions.
        """
        w, h = target_width, target_height
        total_frames = max(1, int(fps * duration_seconds))
        d = total_frames

        # Step 1: Pre-scale and crop to exactly 2x target dimensions maintaining original proportions
        pre_filter = f"scale=w=2*{w}:h=2*{h}:force_original_aspect_ratio=increase,crop=w=2*{w}:h=2*{h}"

        # Step 2: Camera motion trajectories
        if movement == "zoom_out":
            # Smoothly pull back from 1.25x to 1.0x
            zoom_expr = f"zoompan=z='if(eq(on,1),1.25,max(1.0,zoom-0.0015))':d={d}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
        elif movement == "pan_left":
            # Subtle 1.2x zoom with horizontal camera sweep to the right (scene moves left)
            zoom_expr = f"zoompan=z='1.20':d={d}:x='(iw-iw/zoom)*(on/{d})':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
        elif movement == "pan_right":
            # Subtle 1.2x zoom with horizontal camera sweep to the left (scene moves right)
            zoom_expr = f"zoompan=z='1.20':d={d}:x='(iw-iw/zoom)*(1-on/{d})':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
        elif movement == "tilt_up":
            # Subtle 1.2x zoom with upward vertical tilt
            zoom_expr = f"zoompan=z='1.20':d={d}:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-on/{d})':s={w}x{h}:fps={fps}"
        elif movement == "tilt_down":
            # Subtle 1.2x zoom with downward vertical tilt
            zoom_expr = f"zoompan=z='1.20':d={d}:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(on/{d})':s={w}x{h}:fps={fps}"
        else:  # default zoom_in
            # Smooth slow push-in from 1.0x to 1.25x
            zoom_expr = f"zoompan=z='min(zoom+0.0015,1.25)':d={d}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"

        # Step 3: Format and optional fade
        post_filters = [
            f"scale={w}:{h}:force_original_aspect_ratio=decrease",
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
            "setsar=1",
            "format=yuv420p",
        ]

        if apply_fade and duration_seconds >= 1.0:
            fade_len = min(0.35, duration_seconds * 0.15)
            fade_out_st = max(0.0, duration_seconds - fade_len)
            fade_expr = f"fade=t=in:st=0:d={fade_len:.2f},fade=t=out:st={fade_out_st:.2f}:d={fade_len:.2f}"
            post_filters.append(fade_expr)

        return f"{pre_filter},{zoom_expr},{','.join(post_filters)}"

    async def generate_scene_clip(
        self,
        image_path: str,
        prompt: str = "",
        aspect_ratio: str = "9:16",
        duration_seconds: int = 5,
        scene_index: int = 1,
        camera_movement: Optional[str] = None,
        apply_fade: bool = True,
    ) -> str:
        """Generates an animated MP4 video clip from a reference image using FFmpeg.
        
        Args:
            image_path: Absolute or resolvable path to input reference photograph.
            prompt: Contextual scene prompt (used for movement hints or logging).
            aspect_ratio: Video aspect ratio ('9:16', '16:9', '1:1').
            duration_seconds: Clip duration in seconds.
            scene_index: Ordinal index of the scene (1-based).
            camera_movement: Optional specific camera movement ('zoom_in', 'zoom_out', etc.).
            apply_fade: Whether to apply subtle crossfade transitions.
            
        Returns:
            Absolute file path to the generated MP4 clip.
        """
        if not image_path or not os.path.isfile(image_path):
            raise BadRequestError(detail=f"Reference image not found for slideshow generation: {image_path}")

        if duration_seconds <= 0:
            raise BadRequestError(detail=f"Duration must be greater than 0, got {duration_seconds}")

        t_start = time.time()
        timestamp = int(t_start)
        target_w, target_h = get_target_resolution(aspect_ratio)
        movement = self.resolve_camera_movement(camera_movement or prompt, scene_index)

        output_clip_path = os.path.abspath(
            os.path.join(self.output_dir, f"slideshow_scene_{scene_index}_{timestamp}.mp4")
        )

        filtergraph = self.build_filtergraph(
            movement=movement,
            duration_seconds=float(duration_seconds),
            target_width=target_w,
            target_height=target_h,
            fps=30,
            apply_fade=apply_fade,
        )

        cmd = [
            self.ffmpeg_exe,
            "-y",
            "-loop", "1",
            "-i", os.path.abspath(image_path),
            "-vf", filtergraph,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-t", str(duration_seconds),
            "-movflags", "+faststart",
            output_clip_path,
        ]

        logger.info(
            f"Generating FFmpeg slideshow clip for Scene {scene_index} "
            f"(movement={movement}, dur={duration_seconds}s, res={target_w}x{target_h}, out={os.path.basename(output_clip_path)})"
        )

        try:
            # Use asyncio.to_thread to run subprocess.run safely and non-blockingly across all event loops
            # on Windows and Linux (guarantees compatibility with uvicorn --reload / WindowsSelectorEventLoop)
            process = await asyncio.to_thread(
                subprocess.run,
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
            )

            if process.returncode != 0:
                err_detail = (process.stderr or process.stdout or "")[-500:]
                logger.error(f"FFmpeg slideshow generation failed with code {process.returncode}: {err_detail}")
                raise ServiceUnavailableError(detail=f"FFmpeg slideshow animation failed: {err_detail}")

        except Exception as exc:
            if isinstance(exc, (BadRequestError, ServiceUnavailableError)):
                raise
            logger.error(f"Unexpected error running FFmpeg slideshow: {exc}", exc_info=True)
            raise ServiceUnavailableError(detail=f"Slideshow generation failed: {exc}") from exc

        # Verification
        if not os.path.isfile(output_clip_path) or os.path.getsize(output_clip_path) == 0:
            raise ServiceUnavailableError(detail="FFmpeg produced an empty or missing slideshow clip")

        elapsed = time.time() - t_start
        file_size = os.path.getsize(output_clip_path)

        self.last_run_metrics[scene_index] = {
            "provider": self.provider_name,
            "movement": movement,
            "duration_seconds": duration_seconds,
            "aspect_ratio": aspect_ratio,
            "resolution": f"{target_w}x{target_h}",
            "file_size": file_size,
            "generation_time_seconds": round(elapsed, 2),
        }

        logger.info(
            f"Successfully generated slideshow clip for Scene {scene_index} in {elapsed:.2f}s "
            f"({file_size} bytes, {target_w}x{target_h}, movement={movement})"
        )
        return output_clip_path
