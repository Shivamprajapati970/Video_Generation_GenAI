import os
from typing import List, Tuple, Dict, Optional
import imageio_ffmpeg

# Standard target resolutions (width, height)
# All resolutions have even dimensions for H.264 / yuv420p compatibility
RESOLUTION_MAP: Dict[str, Tuple[int, int]] = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (720, 720),
}


def get_target_resolution(aspect_ratio: str) -> Tuple[int, int]:
    """Resolves target width and height for a given aspect ratio string.
    
    Guarantees even dimensions for H.264 encoding.
    """
    clean_ratio = (aspect_ratio or "").strip().lower()
    if clean_ratio in RESOLUTION_MAP:
        return RESOLUTION_MAP[clean_ratio]

    # Handle custom ratios formatted as "W:H" or "W/H"
    for sep in [":", "/"]:
        if sep in clean_ratio:
            try:
                parts = clean_ratio.split(sep)
                rw, rh = float(parts[0]), float(parts[1])
                if rw > 0 and rh > 0:
                    base_dim = 720
                    if rw >= rh:
                        w = int(base_dim * (rw / rh))
                        h = base_dim
                    else:
                        w = base_dim
                        h = int(base_dim * (rh / rw))
                    w = w if w % 2 == 0 else w + 1
                    h = h if h % 2 == 0 else h + 1
                    return (w, h)
            except (ValueError, IndexError, ZeroDivisionError):
                pass

    # Default to portrait 9:16 (720x1280)
    return RESOLUTION_MAP["9:16"]


def build_composition_command(
    clip_paths: List[str],
    voiceover_path: str,
    music_path: str,
    output_path: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 15,
    fps: int = 30,
    video_crf: int = 22,
    video_preset: str = "fast",
    audio_bitrate: str = "192k",
    audio_sample_rate: int = 44100,
    audio_channels: int = 2,
    voiceover_volume: float = 1.0,
    music_volume: float = 0.22,
    fade_out_duration: float = 1.0,
    ffmpeg_exe: Optional[str] = None,
) -> List[str]:
    """Builds an FFmpeg CLI command list for normalizing, concatenating, and mixing media.
    
    Pipeline Steps:
    1. Normalizes all scene clips to target resolution and aspect ratio maintaining proportions.
       Uses scale with force_original_aspect_ratio=decrease and pad with centered letterboxing.
    2. Enforces constant frame rate (30 FPS) and yuv420p pixel format on all clips.
    3. Concatenates video clips in exact scene order.
    4. Mixes voiceover audio (volume 1.0) with background music (volume 0.22).
    5. Loops background music seamlessly if shorter than project duration.
    6. Applies a 1-second fade-out to both voiceover and background music at project end.
    7. Encodes video as H.264 (CRF 22, fast preset) and audio as AAC stereo (44.1 kHz, 192k).
    8. Enforces target project duration (-t).
    9. Applies -movflags +faststart for immediate web streaming.
    
    Returns:
        List[str]: Safe command arguments list suitable for subprocess execution (without shell=True).
    """
    if not clip_paths:
        raise ValueError("At least one video clip path is required for composition.")
    if not voiceover_path:
        raise ValueError("Voiceover audio path is required.")
    if not music_path:
        raise ValueError("Background music path is required.")
    if not output_path:
        raise ValueError("Output video path is required.")
    if duration_seconds <= 0:
        raise ValueError(f"Duration must be a positive integer, got {duration_seconds}.")

    exe = ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe()
    target_w, target_h = get_target_resolution(aspect_ratio)
    clip_count = len(clip_paths)

    # Convert paths to absolute paths to prevent any relative path resolution errors
    abs_clips = [os.path.abspath(p) for p in clip_paths]
    abs_vo = os.path.abspath(voiceover_path)
    abs_music = os.path.abspath(music_path)
    abs_out = os.path.abspath(output_path)

    # Ensure parent output directory exists
    os.makedirs(os.path.dirname(abs_out), exist_ok=True)

    # Build input arguments
    cmd: List[str] = [exe, "-y"]

    # Input 0 to clip_count - 1: Video clips
    for p in abs_clips:
        cmd.extend(["-i", p])

    # Input clip_count: Voiceover audio
    vo_idx = clip_count
    cmd.extend(["-i", abs_vo])

    # Input clip_count + 1: Background music audio (with infinite loop to handle any project duration)
    music_idx = clip_count + 1
    cmd.extend(["-stream_loop", "-1", "-i", abs_music])

    # Build Filtergraph
    filter_parts: List[str] = []

    # 1. Video normalizations: scale without stretching + pad to target dimensions + fps + yuv420p
    for i in range(clip_count):
        video_norm = (
            f"[{i}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps},format=yuv420p[v{i}]"
        )
        filter_parts.append(video_norm)

    # 2. Concatenate all normalized video clips
    concat_inputs = "".join(f"[v{i}]" for i in range(clip_count))
    concat_filter = f"{concat_inputs}concat=n={clip_count}:v=1:a=0[v_concat]"
    filter_parts.append(concat_filter)

    # 3. Audio processing
    # Calculate fade parameters
    total_dur = float(duration_seconds)
    fade_len = min(fade_out_duration, total_dur)
    fade_start = max(0.0, total_dur - fade_len)

    # Voiceover: normalize to 44.1kHz stereo, volume=1.0, fade out at end if longer than duration
    vo_filter = (
        f"[{vo_idx}:a]aformat=sample_fmts=fltp:sample_rates={audio_sample_rate}:channel_layouts=stereo,"
        f"volume={voiceover_volume:.2f},"
        f"afade=t=out:st={fade_start:.2f}:d={fade_len:.2f}[vo]"
    )
    filter_parts.append(vo_filter)

    # Background music: normalize to 44.1kHz stereo, volume=0.22, 1s fade out at end
    bgm_filter = (
        f"[{music_idx}:a]aformat=sample_fmts=fltp:sample_rates={audio_sample_rate}:channel_layouts=stereo,"
        f"volume={music_volume:.2f},"
        f"afade=t=out:st={fade_start:.2f}:d={fade_len:.2f}[bgm]"
    )
    filter_parts.append(bgm_filter)

    # Audio mix: mix voiceover and background music without automatic scaling dip
    mix_filter = "[vo][bgm]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[a_mixed]"
    filter_parts.append(mix_filter)

    filter_complex_str = ";".join(filter_parts)

    # Assemble output options
    cmd.extend([
        "-filter_complex", filter_complex_str,
        "-map", "[v_concat]",
        "-map", "[a_mixed]",
        "-c:v", "libx264",
        "-crf", str(video_crf),
        "-preset", video_preset,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-ar", str(audio_sample_rate),
        "-ac", str(audio_channels),
        "-t", str(duration_seconds),
        "-movflags", "+faststart",
        abs_out,
    ])

    return cmd
