import json
import base64
import os
import uuid
import mimetypes
import logging
import asyncio
import re
from typing import List, Dict, Any, Optional, Tuple

from groq import AsyncGroq
from groq import (
    RateLimitError as GroqRateLimitError,
    APITimeoutError as GroqTimeoutError,
    APIStatusError as GroqAPIStatusError,
    APIConnectionError as GroqConnectionError,
    AuthenticationError as GroqAuthError,
    NotFoundError as GroqNotFoundError,
)
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.scene import ScenePlan, SceneItem, ImageAnalysisItem
from app.ai.base import BaseVisionProvider
from app.services.image_analysis_cache import ImageAnalysisCache
from app.core.exceptions import (
    RateLimitError,
    ProviderTimeoutError,
    ServiceUnavailableError,
    ExternalProviderError,
    BadRequestError,
)

logger = logging.getLogger(__name__)

CAMERA_MOVEMENTS = [
    "Slow forward push-in with subtle pan right",
    "Gentle lateral tracking shot keeping subjects centered",
    "Slow upward crane tilt revealing background ambience",
    "Subtle cinematic arc shot with soft depth of field",
    "Smooth pull-back establishing emotional landscape",
    "Dynamic slow zoom-in highlighting subject expressions",
]


def classify_groq_rate_limit(exc: Exception) -> Tuple[str, str, Optional[float], bool]:
    """Classifies a Groq 429 rate limit into:
    - code: OTPM_LIMIT_EXCEEDED | TPM_LIMIT_EXCEEDED | RPM_LIMIT_EXCEEDED | DAILY_QUOTA_EXCEEDED | RATE_LIMIT_EXCEEDED
    - detail: user-facing explanation
    - retry_after: seconds to wait before retry
    - is_retryable: False if the request fundamentally exceeds model limits
    """
    msg = str(exc)
    msg_lower = msg.lower()

    # 1. Extract retry-after from HTTP headers or response body
    retry_after = None
    response = getattr(exc, "response", None)
    if response and hasattr(response, "headers"):
        ra_header = response.headers.get("retry-after")
        if ra_header:
            try:
                retry_after = float(ra_header)
            except (ValueError, TypeError):
                pass

    if retry_after is None:
        match = re.search(r"try again in (\d+\.?\d*)s", msg, re.IGNORECASE)
        if match:
            try:
                retry_after = float(match.group(1))
            except (ValueError, TypeError):
                pass

    # 2. Check for non-retryable requests exceeding total output token ceiling
    is_retryable = True
    match_limits = re.search(r"limit\s+(\d+),\s+(?:used\s+\d+,\s+)?requested\s+(\d+)", msg, re.IGNORECASE)
    if match_limits:
        limit_val = int(match_limits.group(1))
        req_val = int(match_limits.group(2))
        if "expected output tokens exceed" in msg_lower or (req_val > limit_val and ("output tokens" in msg_lower or "otpm" in msg_lower)):
            is_retryable = False

    # 3. Classify rate limit type
    if "output tokens per minute" in msg_lower or "otpm" in msg_lower:
        code = "OTPM_LIMIT_EXCEEDED"
        detail = "Groq Output Tokens Per Minute (OTPM) rate limit reached. Output tokens exceed model ceiling."
    elif "input tokens per minute" in msg_lower or "itpm" in msg_lower:
        code = "ITPM_LIMIT_EXCEEDED"
        detail = "Groq Input Tokens Per Minute (ITPM) rate limit reached. Please wait for token replenishment."
    elif "tokens per minute" in msg_lower or "tpm" in msg_lower:
        code = "TPM_LIMIT_EXCEEDED"
        detail = "Groq Tokens Per Minute (TPM) rate limit reached. Please wait for token replenishment."
    elif "requests per minute" in msg_lower or "rpm" in msg_lower:
        code = "RPM_LIMIT_EXCEEDED"
        detail = "Groq Requests Per Minute (RPM) rate limit reached. Please wait for a request slot."
    elif "daily quota" in msg_lower or "requests per day" in msg_lower or "rpd" in msg_lower:
        code = "DAILY_QUOTA_EXCEEDED"
        detail = "Groq Free API daily quota reached. Please try again tomorrow or when quota resets."
        is_retryable = False
    else:
        code = "RATE_LIMIT_EXCEEDED"
        detail = "Groq Free API rate limit reached. Please wait a moment and retry."

    return code, detail, retry_after, is_retryable


class GroqVisionProvider(BaseVisionProvider):
    """Groq-hosted free vision understanding and structured scene planner.
    
    Features:
    - Free tier Groq multimodal API access (preferred: qwen/qwen3.8-27b, fallback: llama-3.2-11b-vision-preview).
    - Automatic 3+2 batching for up to 5 reference photos (enforcing Groq's max 3 images per request limit).
    - Preserves database UUIDs and sequencing of images.
    - Deterministic fallback for automated test suites and offline local development.
    - Explicit handling for 429 rate limits, timeouts, and quota exhaustion.
    """

    mock_mode: Optional[bool] = None

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        mock_mode: Optional[bool] = None,
    ):
        if api_key is not None:
            self.api_key = api_key
        else:
            self.api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")
        self.model = model or settings.GROQ_VISION_MODEL or "qwen/qwen3.8-27b"
        self.fallback_model = fallback_model or getattr(settings, "GROQ_FALLBACK_MODEL", "llama-3.2-11b-vision-preview")
        self._mock_mode_param = mock_mode
        self.metrics = {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "rate_limit_errors": 0,
        }

    def record_tokens(self, prompt_tokens: int, completion_tokens: int, total_tokens: int) -> None:
        self.metrics["requests"] += 1
        self.metrics["prompt_tokens"] += prompt_tokens
        self.metrics["completion_tokens"] += completion_tokens
        self.metrics["total_tokens"] += total_tokens

    def record_rate_limit_error(self) -> None:
        self.metrics["rate_limit_errors"] += 1

    def get_token_usage_metrics(self) -> Dict[str, int]:
        return dict(self.metrics)

    def reset_metrics(self) -> None:
        self.metrics = {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "rate_limit_errors": 0,
        }

    @staticmethod
    def compute_scene_durations(num_images: int, total_duration: int) -> List[int]:
        """Calculates exact per-scene durations so all uploaded photos are represented.
        
        Rules:
        - 15s with 5 images: [3, 3, 3, 3, 3] (5 scenes of 3s each, sum=15s)
        - 20s with 5 images: [4, 4, 4, 4, 4] (5 scenes of 4s each, sum=20s)
        - 30s with 5 images: [5, 5, 5, 5, 5, 5] (6 scenes of 5s each, sum=30s, all 5 photos used)
        - 20s with 4 images: [5, 5, 5, 5] (4 scenes of 5s each, sum=20s)
        - 15s with 3 images: [5, 5, 5] (3 scenes of 5s each, sum=15s)
        - Fallback: standard 5s scenes totaling total_duration
        """
        if num_images >= 5:
            if total_duration == 15:
                return [3, 3, 3, 3, 3]
            elif total_duration == 20:
                return [4, 4, 4, 4, 4]
            elif total_duration == 30:
                return [5, 5, 5, 5, 5, 5]
        elif num_images == 4:
            if total_duration == 20:
                return [5, 5, 5, 5]
            elif total_duration == 15:
                return [5, 5, 5]
            elif total_duration == 30:
                return [5, 5, 5, 5, 5, 5]
        elif num_images == 3:
            if total_duration == 15:
                return [5, 5, 5]
            elif total_duration == 20:
                return [5, 5, 5, 5]
            elif total_duration == 30:
                return [5, 5, 5, 5, 5, 5]

        num_scenes = max(3, min(6, total_duration // 5))
        return [5] * num_scenes

    @staticmethod
    def get_scene_generation_type(scene_index: int, total_scenes: int) -> str:
        """Determines default generation type for scenes.
        
        Defaults to 'wan_video' so all scenes attempt AI video generation by default.
        If Wan 2.2 is unavailable or rate-limited, Phase 9 automatic slideshow fallback activates.
        """
        return "wan_video"

    @property
    def effective_mock_mode(self) -> bool:
        if self._mock_mode_param is not None:
            return self._mock_mode_param
        if GroqVisionProvider.mock_mode is not None:
            return GroqVisionProvider.mock_mode
        return settings.is_testing

    def is_configured(self) -> bool:
        """Checks if a valid, non-placeholder Groq API key is present."""
        if not self.api_key:
            return False
        clean_key = self.api_key.strip().lower()
        if clean_key in ("", "your_groq_free_api_key_here", "your_groq_api_key_here", "placeholder", "none"):
            return False
        return True

    def _encode_image_to_base64(self, file_path: str) -> Optional[str]:
        """Encodes an image to a base64 data URI."""
        if not os.path.exists(file_path):
            return None
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "image/jpeg"
        try:
            with open(file_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:{mime_type};base64,{encoded}"
        except Exception as exc:
            logger.warning(f"Failed to read image at {file_path} for base64 encoding: {exc}")
            return None

    async def analyze_image_batch(
        self,
        batch_images: List[Dict[str, Any]],
        user_description: str,
        occasion: str,
    ) -> List[ImageAnalysisItem]:
        """Analyzes a batch of up to 3 images using Groq vision API.
        
        Args:
            batch_images: List of dicts with keys: image_id, file_path, original_filename, order_index
            user_description: User provided context or instructions
            occasion: Project occasion
        """
        if len(batch_images) > 3:
            raise ValueError(f"Groq vision batch exceeds limit of 3 images (received {len(batch_images)})")

        if not batch_images:
            return []

        # If running in mock mode or automated testing without real Groq credentials
        if self.effective_mock_mode:
            return self._build_deterministic_image_analyses(batch_images, user_description, occasion)

        if not self.is_configured():
            raise BadRequestError(
                detail="Groq API key is not configured. Please set a valid free GROQ_API_KEY in backend/.env."
            )

        client = AsyncGroq(api_key=self.api_key, max_retries=5)

        # Build prompt instructing Groq to extract structured analysis per image
        image_metadata_summary = [
            f"- Image {idx + 1}: ID={img['image_id']}, filename='{img.get('original_filename', f'photo_{idx + 1}')}'"
            for idx, img in enumerate(batch_images)
        ]

        system_prompt = (
            "You are a master cinematic director and computer vision expert. "
            "You analyze reference photos for video generation and output strictly valid JSON."
        )

        schema_examples = ",\n".join([
            f'  {{\n'
            f'    "image_id": "{img["image_id"]}",\n'
            f'    "original_filename": "{img.get("original_filename", f"photo_{idx + 1}")}",\n'
            f'    "order_index": {img.get("order_index", idx)},\n'
            f'    "subjects": ["visible subject 1", "visible subject 2"],\n'
            f'    "environment": "concise physical setting",\n'
            f'    "details": ["key detail 1", "key detail 2"],\n'
            f'    "mood": "emotional vibe"\n'
            f'  }}'
            for idx, img in enumerate(batch_images)
        ])

        # Concise prompt requesting essential details only (Requirement 2)
        user_content: List[Dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Analyze the attached {len(batch_images)} reference photo(s) with strict visual fidelity.\n"
                    f"Occasion: {occasion}\n"
                    f"User Story/Context: {user_description}\n\n"
                    f"STRICT VISUAL GROUNDING RULE: Accurately distinguish the medium (realistic photograph vs. graphic illustration). Describe ONLY what is genuinely visible. Do NOT invent, assume, or hallucinate people, objects, actions, or settings not clearly visible.\n\n"
                    f"Image references:\n" + "\n".join(image_metadata_summary) + "\n\n"
                    f"For each photo in the exact order attached, extract essential details:\n"
                    f"- image_id (exact provided ID)\n"
                    f"- original_filename\n"
                    f"- order_index (0-indexed integer)\n"
                    f"- subjects (list of 1-3 visible subjects/people/objects)\n"
                    f"- environment (concise background and physical setting, max 12 words)\n"
                    f"- details (list of 1-2 key visible details, max 5 words each)\n"
                    f"- mood (1-3 words emotional vibe and atmosphere)\n\n"
                    f"You MUST return an analysis for each of the {len(batch_images)} attached image(s). The 'analyses' array MUST contain exactly {len(batch_images)} items.\n\n"
                    f"Return ONLY a single valid JSON object strictly matching this schema:\n"
                    f'{{\n"analyses": [\n{schema_examples}\n]\n}}'
                ),
            }
        ]

        # Attach image base64 data
        for img in batch_images:
            data_uri = self._encode_image_to_base64(img.get("file_path", ""))
            if data_uri:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": data_uri}
                })

        # Calculate max_tokens: strictly bounded to avoid OTPM limits while ensuring complete JSON (Requirement 1 & 7)
        calc_max_tokens = min(500, max(280, 240 * len(batch_images)))

        # Attempt with primary model, fallback on model failure
        models_to_try = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models_to_try.append(self.fallback_model)

        last_error = None
        for current_model in models_to_try:
            attempts = 3
            for attempt in range(attempts):
                try:
                    response = await client.chat.completions.create(
                        model=current_model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.5,
                        max_tokens=calc_max_tokens,
                        max_completion_tokens=calc_max_tokens,
                    )
                    if hasattr(response, "usage") and response.usage:
                        u = response.usage
                        p_tok = getattr(u, "prompt_tokens", 0) or 0
                        c_tok = getattr(u, "completion_tokens", 0) or 0
                        t_tok = getattr(u, "total_tokens", 0) or (p_tok + c_tok)
                        self.record_tokens(p_tok, c_tok, t_tok)
                        logger.info(
                            f"Groq Vision batch API ({len(batch_images)} images) on {current_model}: "
                            f"prompt_tokens={p_tok}, completion_tokens={c_tok}, total_tokens={t_tok}"
                        )
                    raw_json = response.choices[0].message.content
                    clean_json = raw_json.strip()
                    if clean_json.startswith("```json"):
                        clean_json = clean_json[7:]
                    elif clean_json.startswith("```"):
                        clean_json = clean_json[3:]
                    if clean_json.endswith("```"):
                        clean_json = clean_json[:-3]
                    clean_json = clean_json.strip()

                    data = json.loads(clean_json)
                    if isinstance(data, list):
                        analyses_list = data
                    elif isinstance(data, dict):
                        analyses_list = data.get("analyses") or data.get("images") or data.get("items") or [data]
                    else:
                        analyses_list = []

                    validated_items = []
                    for i in range(len(batch_images)):
                        img_meta = batch_images[i]
                        if i < len(analyses_list) and isinstance(analyses_list[i], dict):
                            item = dict(analyses_list[i])
                            item["image_id"] = str(img_meta["image_id"])
                            item["original_filename"] = img_meta.get("original_filename", item.get("original_filename", f"photo_{i}"))
                            item["order_index"] = img_meta.get("order_index", i)
                            validated_items.append(ImageAnalysisItem.model_validate(item))
                        else:
                            # Fallback if AI response omitted one of the images in the batch
                            validated_items.append(
                                ImageAnalysisItem(
                                    image_id=str(img_meta["image_id"]),
                                    original_filename=img_meta.get("original_filename", f"photo_{i + 1}"),
                                    order_index=img_meta.get("order_index", i),
                                    subjects=[f"Main subject in {img_meta.get('original_filename', f'photo_{i + 1}')}"],
                                    environment="Celebratory ambient background",
                                    details=["Cherished moment", "Festive atmosphere"],
                                    mood="Joyful and celebratory",
                                )
                            )
                    return validated_items

                except GroqRateLimitError as rle:
                    self.record_rate_limit_error()
                    code, detail, retry_after, is_retryable = classify_groq_rate_limit(rle)
                    if not is_retryable:
                        logger.error(f"Groq non-retryable rate limit on {current_model} ({code}): {detail} - {rle}")
                        raise RateLimitError(detail=detail, code=code, retry_after=int(retry_after) if retry_after else None) from rle

                    if attempt < attempts - 1:
                        wait_s = retry_after if (retry_after is not None and retry_after > 0) else (15 * (attempt + 1))
                        wait_s = min(60.0, max(3.0, wait_s + 1.0))
                        logger.warning(
                            f"Groq Rate limit ({code}) hit during image analysis on {current_model}. "
                            f"Respecting Retry-After: waiting {wait_s:.1f}s before retry (attempt {attempt + 1}/{attempts})..."
                        )
                        if not self.effective_mock_mode and os.getenv("TESTING", "false").lower() != "true":
                            await asyncio.sleep(wait_s)
                        continue
                    logger.error(f"Groq Rate Limit exceeded on {current_model} after {attempts} attempts ({code}): {rle}")
                    raise RateLimitError(
                        detail=detail,
                        code=code,
                        retry_after=int(retry_after) if retry_after else None,
                    ) from rle
                except GroqTimeoutError as te:
                    logger.error(f"Groq timeout on {current_model}: {te}")
                    raise ProviderTimeoutError(
                        detail="Groq AI vision request timed out. Please retry."
                    ) from te
                except GroqAuthError as ae:
                    logger.error(f"Groq authentication error on {current_model}: {ae}")
                    raise BadRequestError(
                        detail="Invalid Groq API key. Please check that your free GROQ_API_KEY in backend/.env is valid."
                    ) from ae
                except GroqNotFoundError as nfe:
                    logger.error(f"Groq model not found on {current_model}: {nfe}")
                    last_error = nfe
                    break
                except (GroqConnectionError, GroqAPIStatusError) as ase:
                    if getattr(ase, "status_code", None) in (413, 429) or "rate_limit_exceeded" in str(ase):
                        code, detail, retry_after, is_retryable = classify_groq_rate_limit(ase)
                        if not is_retryable:
                            logger.error(f"Groq non-retryable rate limit on {current_model} ({code}): {detail} - {ase}")
                            raise RateLimitError(detail=detail, code=code, retry_after=int(retry_after) if retry_after else None) from ase
                        if attempt < attempts - 1:
                            wait_s = retry_after if (retry_after is not None and retry_after > 0) else (15 * (attempt + 1))
                            wait_s = min(60.0, max(3.0, wait_s + 1.0))
                            logger.warning(
                                f"Groq status {getattr(ase, 'status_code', 429)} on {current_model} ({code}). Waiting {wait_s:.1f}s to retry..."
                            )
                            if not self.effective_mock_mode and os.getenv("TESTING", "false").lower() != "true":
                                await asyncio.sleep(wait_s)
                            continue
                        raise RateLimitError(detail=detail, code=code, retry_after=int(retry_after) if retry_after else None) from ase
                    if "json_validate_failed" in str(ase).lower() or "max completion tokens reached" in str(ase).lower():
                        calc_max_tokens = min(600, calc_max_tokens + 100)
                        logger.warning(
                            f"Groq JSON validate failed on {current_model} (attempt {attempt + 1}/{attempts}). "
                            f"Increased calc_max_tokens to {calc_max_tokens}. Retrying..."
                        )
                        if attempt < attempts - 1:
                            if not self.effective_mock_mode and os.getenv("TESTING", "false").lower() != "true":
                                await asyncio.sleep(2)
                            continue
                    logger.warning(f"Groq API error on model {current_model}: {ase}")
                    last_error = ase
                    break
                except (json.JSONDecodeError, ValidationError) as ve:
                    logger.warning(f"Failed to parse Groq response JSON on {current_model}: {ve}")
                    raise ServiceUnavailableError(
                        detail="Failed to parse valid structured JSON from Groq AI response."
                    ) from ve

        # If models failed
        if isinstance(last_error, GroqNotFoundError):
            raise ServiceUnavailableError(
                detail=f"Groq model '{self.model}' is invalid or no longer available."
            )
        raise ServiceUnavailableError(
            detail=f"Groq Vision service failed to analyze images: {last_error}"
        )

    async def synthesize_scene_plan(
        self,
        combined_analyses: List[ImageAnalysisItem],
        project_metadata: Dict[str, Any],
    ) -> ScenePlan:
        """Synthesizes all analyzed images into a coherent multi-scene video plan."""
        occasion = project_metadata.get("occasion", "Celebration")
        user_description = project_metadata.get("description", "")
        duration = project_metadata.get("duration", 15)
        aspect_ratio = project_metadata.get("aspect_ratio", "9:16")
        music_mood = project_metadata.get("music_mood", "Cinematic")
        custom_music_prompt = project_metadata.get("custom_music_prompt", "")

        durations = self.compute_scene_durations(len(combined_analyses), duration)
        num_scenes = len(durations)
        available_image_ids = [a.image_id for a in combined_analyses]

        scene_configs = []
        for i in range(num_scenes):
            if i >= len(available_image_ids):
                # Intentional closing scene reference: reprise the opening hero image
                ref_id = available_image_ids[0]
            else:
                ref_id = available_image_ids[i]
            dur = durations[i]
            gen_type = self.get_scene_generation_type(i, num_scenes)
            scene_configs.append({
                "scene_number": i + 1,
                "duration_seconds": dur,
                "reference_image_id": ref_id,
                "generation_type": gen_type,
            })

        if self.effective_mock_mode:
            return self._build_deterministic_scene_plan(
                analyses=combined_analyses,
                user_description=user_description,
                occasion=occasion,
                duration=duration,
                aspect_ratio=aspect_ratio,
                music_mood=music_mood,
                custom_music_prompt=custom_music_prompt,
                num_scenes=num_scenes,
                durations=durations,
            )

        if not self.is_configured():
            raise BadRequestError(
                detail="Groq API key is not configured. Please set a valid free GROQ_API_KEY in backend/.env."
            )

        client = AsyncGroq(api_key=self.api_key, max_retries=5)

        analyses_json = json.dumps([a.model_dump() for a in combined_analyses], separators=(',', ':'))

        scene_skeletons = ",\n    ".join([
            f'{{\n'
            f'      "scene_number": {sc["scene_number"]},\n'
            f'      "title": "Scene {sc["scene_number"]} Title",\n'
            f'      "duration_seconds": {sc["duration_seconds"]},\n'
            f'      "generation_type": "{sc["generation_type"]}",\n'
            f'      "reference_image_ids": ["{sc["reference_image_id"]}"],\n'
            f'      "visual_description": "Vivid grounded visuals (~15 words)",\n'
            f'      "scene_objective": "Narrative goal",\n'
            f'      "camera_movement": "{CAMERA_MOVEMENTS[(sc["scene_number"] - 1) % len(CAMERA_MOVEMENTS)]}",\n'
            f'      "motion_prompt": "Wan 2.2 motion synthesis prompt (~15 words)",\n'
            f'      "transition": "fade",\n'
            f'      "narration_text": "Complete standalone voiceover sentence {sc["scene_number"]}."\n'
            f'    }}'
            for sc in scene_configs
        ])

        system_prompt = (
            "You are an award-winning cinematic director and AI video producer. "
            "You create tightly timed, emotionally compelling video scene plans that drive Wan 2.2 video generation, "
            "Chatterbox voiceover narration, and ACE-Step music synthesis. Always respond with strict valid JSON."
        )

        prompt = (
            f"Create a personalized {duration}-second video plan for the occasion '{occasion}'.\n"
            f"Aspect ratio: {aspect_ratio}\n"
            f"Music mood: {music_mood}\n"
            f"User Story/Instructions: {user_description}\n"
            f"{f'Custom music guidance: {custom_music_prompt}' if custom_music_prompt else ''}\n\n"
            f"Available analyzed reference images:\n{analyses_json}\n\n"
            f"Strict requirements:\n"
            f"1. Divide the video into exactly {num_scenes} scenes with these EXACT durations: {durations} (totaling {duration} seconds).\n"
            f"2. Image Selection & Rationale:\n"
            f"   - Map reference photos in sequence to the {num_scenes} scenes from: {available_image_ids}\n"
            f"   - Ensure all uploaded reference photos appear in the final video scenes.\n"
            f"   - If all images are selected, set 'unselected_images_explanation' to null.\n"
            f"   - If any uploaded image could not be selected due to duration constraints, provide an explicit constructive reason in 'unselected_images_explanation'.\n"
            f"3. Strict Visual Grounding & Accurate Medium Representation (Wan 2.2 Ready):\n"
            f"   - Accurately distinguish the medium (realistic photograph vs graphic illustration). Describe ONLY what is genuinely visible. Do NOT invent people, objects, activities, or events.\n"
            f"   - Every 'visual_description' and 'motion_prompt' MUST be strictly grounded in the actual reference photo.\n"
            f"   - The 'motion_prompt' should describe realistic camera movement and natural motion of visible elements.\n"
            f"4. Narration & Pacing Consistency:\n"
            f"   - Each scene's 'narration_text' MUST be a complete, grammatically self-contained sentence.\n"
            f"   - Voiceover length must match scene duration: around 6-10 words for 3s scenes, 8-12 words for 4s scenes, 10-14 words for 5-6s scenes.\n"
            f"   - 'full_narration_script' MUST be the exact word-for-word composite of: opening_narration, scene 1 narration, ..., scene {num_scenes} narration, and closing_narration.\n"
            f"5. Each scene MUST have:\n"
            f"   - scene_number: 1 to {num_scenes}\n"
            f"   - title: Short expressive scene title\n"
            f"   - duration_seconds: exactly matching the specified scene duration ({durations})\n"
            f"   - generation_type: 'wan_video' or 'animated_still'\n"
            f"   - reference_image_ids: list containing the assigned image ID chosen from: {available_image_ids}\n"
            f"   - visual_description: vivid grounded visual description (compact, ~15-20 words)\n"
            f"   - scene_objective: concise emotional or narrative objective\n"
            f"   - camera_movement: specific camera instruction\n"
            f"   - motion_prompt: detailed visual motion prompt (compact, ~15-20 words)\n"
            f"   - transition: transition into this scene ('fade', 'dissolve', 'wipe', 'cut')\n"
            f"   - narration_text: non-empty spoken voiceover line\n"
            f"6. Overall video info:\n"
            f"   - video_concept, occasion, story_summary, visual_style, emotional_tone, total_duration, aspect_ratio\n"
            f"   - full_narration_script: complete spoken script combining opening, scene voiceovers, and closing\n"
            f"   - opening_narration: opening spoken hook sentence\n"
            f"   - closing_narration: memorable final sign-off sentence\n"
            f"   - music_mood, background_music_prompt, unselected_images_explanation\n\n"
            f"Return ONLY a single valid JSON object strictly conforming to this schema:\n"
            f'{{\n'
            f'  "video_concept": "Concept Title",\n'
            f'  "occasion": "{occasion}",\n'
            f'  "story_summary": "Unified story summary",\n'
            f'  "visual_style": "Color palette and cinematography",\n'
            f'  "emotional_tone": "Emotional atmosphere",\n'
            f'  "total_duration": {duration},\n'
            f'  "aspect_ratio": "{aspect_ratio}",\n'
            f'  "full_narration_script": "Complete spoken voiceover script combining opening, all scene narrations, and closing",\n'
            f'  "opening_narration": "Opening spoken hook sentence",\n'
            f'  "closing_narration": "Closing memorable sign-off sentence",\n'
            f'  "music_mood": "{music_mood}",\n'
            f'  "background_music_prompt": "Soundtrack description",\n'
            f'  "unselected_images_explanation": null,\n'
            f'  "scenes": [\n    {scene_skeletons}\n  ]\n'
            f'}}'
        )

        models_to_try = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models_to_try.append(self.fallback_model)

        last_error = None
        for current_model in models_to_try:
            attempts = 3
            for attempt in range(attempts):
                try:
                    response = await client.chat.completions.create(
                        model=current_model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.6,
                        max_tokens=2500,
                        max_completion_tokens=2500,
                    )
                    if hasattr(response, "usage") and response.usage:
                        u = response.usage
                        p_tok = getattr(u, "prompt_tokens", 0) or 0
                        c_tok = getattr(u, "completion_tokens", 0) or 0
                        t_tok = getattr(u, "total_tokens", 0) or (p_tok + c_tok)
                        self.record_tokens(p_tok, c_tok, t_tok)
                        logger.info(
                            f"Groq ScenePlan synthesis on {current_model}: "
                            f"prompt_tokens={p_tok}, completion_tokens={c_tok}, total_tokens={t_tok}"
                        )

                    raw_json = response.choices[0].message.content
                    clean_json = raw_json.strip()
                    if clean_json.startswith("```json"):
                        clean_json = clean_json[7:]
                    elif clean_json.startswith("```"):
                        clean_json = clean_json[3:]
                    if clean_json.endswith("```"):
                        clean_json = clean_json[:-3]
                    clean_json = clean_json.strip()

                    data = json.loads(clean_json)
                    if not isinstance(data, dict):
                        raise ValueError("Parsed JSON is not an object")

                    # Normalize and ensure exact num_scenes and durations
                    raw_scenes = data.get("scenes") or []
                    if not isinstance(raw_scenes, list):
                        raw_scenes = []

                    # If fewer scenes than required, supplement missing scenes
                    if len(raw_scenes) < num_scenes:
                        for idx in range(len(raw_scenes), num_scenes):
                            cfg = scene_configs[idx]
                            ref_analysis = combined_analyses[min(idx, len(combined_analyses) - 1)]
                            subj = ref_analysis.subjects[0] if ref_analysis.subjects else "Celebration milestone"
                            env = ref_analysis.environment or "festive background"
                            raw_scenes.append({
                                "scene_number": cfg["scene_number"],
                                "title": f"Milestone Chapter {cfg['scene_number']}",
                                "duration_seconds": cfg["duration_seconds"],
                                "generation_type": cfg["generation_type"],
                                "reference_image_ids": [cfg["reference_image_id"]],
                                "visual_description": f"Cinematic perspective of {subj} in {env}.",
                                "scene_objective": f"Highlight meaningful celebration moments from {ref_analysis.original_filename}.",
                                "camera_movement": CAMERA_MOVEMENTS[idx % len(CAMERA_MOVEMENTS)],
                                "motion_prompt": f"Gentle cinematic camera motion focusing on {subj} in {env}.",
                                "transition": "fade",
                                "narration_text": f"Every shared moment and genuine smile creates memories that linger for a lifetime.",
                            })
                    elif len(raw_scenes) > num_scenes:
                        raw_scenes = raw_scenes[:num_scenes]

                    # Enforce exact durations, sequential numbering, 1:1 reference image mapping, generation_type, and unique narration
                    seen_narrations = set()
                    for idx, sc in enumerate(raw_scenes):
                        cfg = scene_configs[idx]
                        sc["scene_number"] = cfg["scene_number"]
                        sc["duration_seconds"] = cfg["duration_seconds"]
                        sc["generation_type"] = cfg["generation_type"]
                        # Enforce 1:1 reference image mapping: Scene i strictly uses uploaded Image i
                        sc["reference_image_ids"] = [cfg["reference_image_id"]]
                        n_text = str(sc.get("narration_text", "")).strip()
                        if not n_text or n_text in seen_narrations:
                            n_text = f"Cherished milestone memories in scene {idx + 1} reflect the heartwarming spirit of this journey."
                        seen_narrations.add(n_text)
                        sc["narration_text"] = n_text

                    data["scenes"] = raw_scenes
                    data["total_duration"] = duration

                    # Validate unselected_images_explanation
                    used_ids = set()
                    for sc in raw_scenes:
                        for rid in sc.get("reference_image_ids", []):
                            used_ids.add(str(rid))
                    all_ids = {str(a.image_id) for a in combined_analyses}
                    if not (all_ids - used_ids):
                        data["unselected_images_explanation"] = None

                    return ScenePlan.model_validate(data)

                except GroqRateLimitError as rle:
                    self.record_rate_limit_error()
                    code, detail, retry_after, is_retryable = classify_groq_rate_limit(rle)
                    if not is_retryable:
                        logger.error(f"Groq non-retryable rate limit on {current_model} during synthesis ({code}): {detail} - {rle}")
                        raise RateLimitError(detail=detail, code=code, retry_after=int(retry_after) if retry_after else None) from rle

                    if attempt < attempts - 1:
                        wait_s = retry_after if (retry_after is not None and retry_after > 0) else (15 * (attempt + 1))
                        wait_s = min(60.0, max(3.0, wait_s + 1.0))
                        logger.warning(
                            f"Groq Rate limit ({code}) hit during scene plan synthesis on {current_model}. "
                            f"Respecting Retry-After: waiting {wait_s:.1f}s for token bucket replenishment (attempt {attempt + 1}/{attempts})..."
                        )
                        if not self.effective_mock_mode and os.getenv("TESTING", "false").lower() != "true":
                            await asyncio.sleep(wait_s)
                        continue
                    logger.error(f"Groq Rate Limit exceeded during scene plan synthesis on {current_model} ({code}): {rle}")
                    raise RateLimitError(
                        detail=detail,
                        code=code,
                        retry_after=int(retry_after) if retry_after else None,
                    ) from rle
                except GroqTimeoutError as te:
                    logger.error(f"Groq timeout during scene plan synthesis on {current_model}: {te}")
                    raise ProviderTimeoutError(
                        detail="Groq AI request timed out during scene planning."
                    ) from te
                except GroqAuthError as ae:
                    logger.error(f"Groq authentication error during scene plan synthesis on {current_model}: {ae}")
                    raise BadRequestError(
                        detail="Invalid Groq API key. Please check that your free GROQ_API_KEY in backend/.env is valid."
                    ) from ae
                except GroqNotFoundError as nfe:
                    logger.error(f"Groq model not found during scene plan synthesis on {current_model}: {nfe}")
                    last_error = nfe
                    break
                except (GroqConnectionError, GroqAPIStatusError) as ase:
                    if getattr(ase, "status_code", None) in (413, 429) or "rate_limit_exceeded" in str(ase):
                        code, detail, retry_after, is_retryable = classify_groq_rate_limit(ase)
                        if not is_retryable:
                            logger.error(f"Groq non-retryable rate limit on {current_model} ({code}): {detail} - {ase}")
                            raise RateLimitError(detail=detail, code=code, retry_after=int(retry_after) if retry_after else None) from ase
                        if attempt < attempts - 1:
                            wait_s = retry_after if (retry_after is not None and retry_after > 0) else (15 * (attempt + 1))
                            wait_s = min(60.0, max(3.0, wait_s + 1.0))
                            logger.warning(
                                f"Groq status {getattr(ase, 'status_code', 429)} on {current_model} ({code}). Waiting {wait_s:.1f}s to retry..."
                            )
                            if not self.effective_mock_mode and os.getenv("TESTING", "false").lower() != "true":
                                await asyncio.sleep(wait_s)
                            continue
                        raise RateLimitError(detail=detail, code=code, retry_after=int(retry_after) if retry_after else None) from ase
                    logger.warning(f"Groq API error on model {current_model}: {ase}")
                    last_error = ase
                    break
                except (json.JSONDecodeError, ValidationError, ValueError) as ve:
                    logger.warning(f"Failed to parse ScenePlan from Groq on {current_model} (attempt {attempt + 1}/{attempts}): {ve}")
                    if attempt < attempts - 1:
                        if not self.effective_mock_mode and os.getenv("TESTING", "false").lower() != "true":
                            await asyncio.sleep(2)
                        continue
                    raise ServiceUnavailableError(
                        detail="Failed to parse valid structured JSON from Groq AI response."
                    ) from ve

        if isinstance(last_error, GroqNotFoundError):
            raise ServiceUnavailableError(
                detail=f"Groq model '{self.model}' is invalid or no longer available."
            )
        raise ServiceUnavailableError(
            detail=f"Groq AI service failed to synthesize scene plan: {last_error}"
        )

    async def analyze_and_plan(
        self,
        image_paths: List[str],
        user_description: str,
        occasion: str,
        duration: int,
        aspect_ratio: str = "9:16",
        image_metadata: Optional[List[Dict[str, Any]]] = None,
        music_mood: str = "Cinematic",
        custom_music_prompt: Optional[str] = None,
    ) -> ScenePlan:
        """Full orchestration pipeline:
        1. Prepares image metadata with persistent IDs and order.
        2. Splits up to 5 images into batches of max 3 (e.g. 3+2 batching).
        3. Analyzes each batch via Groq vision.
        4. Combines analyses.
        5. Synthesizes complete ScenePlan.
        """
        if not image_paths and not image_metadata:
            raise BadRequestError(detail="No reference images provided for analysis and planning")

        # Normalize image metadata
        norm_images: List[Dict[str, Any]] = []
        if image_metadata:
            for idx, item in enumerate(image_metadata):
                norm_images.append({
                    "image_id": str(item.get("image_id", uuid.uuid4())),
                    "file_path": item.get("file_path", image_paths[idx] if idx < len(image_paths) else ""),
                    "original_filename": item.get("original_filename", os.path.basename(item.get("file_path", f"image_{idx + 1}"))),
                    "order_index": item.get("order_index", idx),
                })
        else:
            for idx, path in enumerate(image_paths):
                norm_images.append({
                    "image_id": str(uuid.uuid4()),
                    "file_path": path,
                    "original_filename": os.path.basename(path),
                    "order_index": idx,
                })

        total_imgs = len(norm_images)

        # Check cache for each image first (Requirement A.4 & A.5)
        cached_analyses: Dict[str, ImageAnalysisItem] = {}
        uncached_images: List[Dict[str, Any]] = []

        for img in norm_images:
            cached_item = ImageAnalysisCache.get(
                file_path=img.get("file_path", ""),
                image_id=str(img.get("image_id", "")),
                original_filename=img.get("original_filename", ""),
                order_index=img.get("order_index", 0),
            )
            if cached_item is not None:
                cached_analyses[str(img["image_id"])] = cached_item
            else:
                uncached_images.append(img)

        logger.info(
            f"Image analysis cache status for {total_imgs} images: "
            f"{len(cached_analyses)} cached, {len(uncached_images)} uncached"
        )

        newly_analyzed: Dict[str, ImageAnalysisItem] = {}
        if uncached_images:
            batch_size = max(1, min(getattr(settings, "GROQ_VISION_BATCH_SIZE", 3), 3))
            batches: List[List[Dict[str, Any]]] = []
            if batch_size == 3:
                if len(uncached_images) <= 3:
                    batches.append(uncached_images)
                else:
                    batches.append(uncached_images[:3])
                    batches.append(uncached_images[3:5])
            else:
                batches = [
                    uncached_images[i:i + batch_size] for i in range(0, len(uncached_images), batch_size)
                ]

            logger.info(f"Analyzing {len(uncached_images)} uncached images across {len(batches)} Groq batch(es) (sizes: {[len(b) for b in batches]}, batch_size={batch_size})")

            for batch_idx, batch in enumerate(batches):
                if batch_idx > 0 and not self.effective_mock_mode:
                    pause_s = 14 if batch_size == 1 else 25
                    logger.info(f"Pausing {pause_s}s before Groq batch {batch_idx + 1} for Free Tier token replenishment...")
                    await asyncio.sleep(pause_s)

                logger.info(f"Executing Groq vision batch {batch_idx + 1}/{len(batches)} ({len(batch)} images)")
                batch_result = await self.analyze_image_batch(
                    batch_images=batch,
                    user_description=user_description,
                    occasion=occasion,
                )
                for item in batch_result:
                    newly_analyzed[str(item.image_id)] = item
                    matching_img = next((img for img in batch if str(img["image_id"]) == str(item.image_id)), None)
                    if matching_img and matching_img.get("file_path"):
                        ImageAnalysisCache.put(matching_img["file_path"], item)

        # Assemble combined_analyses preserving original input sequence
        combined_analyses: List[ImageAnalysisItem] = []
        for img in norm_images:
            img_id_str = str(img["image_id"])
            if img_id_str in cached_analyses:
                combined_analyses.append(cached_analyses[img_id_str])
            elif img_id_str in newly_analyzed:
                combined_analyses.append(newly_analyzed[img_id_str])

        if uncached_images and not self.effective_mock_mode:
            pause_synthesis = 20 if len(uncached_images) <= 3 else 25
            logger.info(f"Pausing {pause_synthesis}s before ScenePlan synthesis for Free Tier token replenishment...")
            await asyncio.sleep(pause_synthesis)

        project_metadata = {
            "occasion": occasion,
            "description": user_description,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "music_mood": music_mood,
            "custom_music_prompt": custom_music_prompt,
        }

        logger.info(f"Synthesizing {duration}s ScenePlan from {len(combined_analyses)} analyzed images")
        return await self.synthesize_scene_plan(combined_analyses, project_metadata)

    def _build_deterministic_image_analyses(
        self,
        batch_images: List[Dict[str, Any]],
        user_description: str,
        occasion: str,
    ) -> List[ImageAnalysisItem]:
        """Deterministic image analysis builder for tests and offline usage."""
        analyses = []
        for img in batch_images:
            idx = img.get("order_index", 0)
            img_id = str(img.get("image_id", uuid.uuid4()))
            filename = img.get("original_filename", f"photo_{idx + 1}.jpg")

            analyses.append(
                ImageAnalysisItem(
                    image_id=img_id,
                    original_filename=filename,
                    order_index=idx,
                    subjects=[f"Main subject in {filename}", "Family and friends"],
                    environment=f"Warm decorated {occasion.lower()} venue with elegant ambient background",
                    details=["Festive decorations", "Joyful natural smiles", "Soft golden lighting accents"],
                    activities=f"Celebrating and sharing memorable {occasion.lower()} moments",
                    mood=f"Joyful, intimate, and deeply celebratory {occasion.lower()} atmosphere",
                    lighting_composition="Cinematic soft lighting, portrait depth of field, balanced thirds framing",
                    cinematic_presentation="Slow graceful push-in with gentle lens blur transition",
                    story_role=f"Key milestone snapshot representing chapter {idx + 1} of the {occasion.lower()} story",
                )
            )
        return analyses

    def _build_deterministic_scene_plan(
        self,
        analyses: List[ImageAnalysisItem],
        user_description: str,
        occasion: str,
        duration: int,
        aspect_ratio: str,
        music_mood: str,
        custom_music_prompt: Optional[str],
        num_scenes: Optional[int] = None,
        durations: Optional[List[int]] = None,
    ) -> ScenePlan:
        """Deterministic plan generator used for automated verification and offline development."""
        if durations is None:
            durations = self.compute_scene_durations(len(analyses), duration)
        num_scenes = len(durations)
        num_images = max(1, len(analyses))
        scenes: List[SceneItem] = []
        full_narration_lines: List[str] = []

        # Unique standalone narration sentences for natural flow without awkward splits
        narration_templates = [
            f"Every genuine smile and glance captures the timeless spirit of this {occasion.lower()}.",
            f"Cherished milestones and shared adventures reflect a lifetime of unforgettable memories.",
            f"Under warm ambient skies, each quiet moment brings friends and family closer together.",
            f"Laughter and vibrant energy fill the air as we celebrate this extraordinary journey.",
            f"Golden light illuminates the joyful bonds that make this milestone truly unforgettable.",
            f"Surrounded by warmth and love, we honor the memories that shape our lasting story.",
        ]

        for i in range(num_scenes):
            if i >= num_images:
                # Reprise hero image for closing scene
                img_ref = analyses[0]
            else:
                img_ref = analyses[i]
            scene_num = i + 1
            dur = durations[i]
            gen_type = self.get_scene_generation_type(i, num_scenes)
            cam_movement = CAMERA_MOVEMENTS[i % len(CAMERA_MOVEMENTS)]
            narration = narration_templates[i % len(narration_templates)]
            full_narration_lines.append(narration)

            scenes.append(
                SceneItem(
                    scene_number=scene_num,
                    title=f"Scene {scene_num}: {occasion} Chapter {scene_num}",
                    duration_seconds=dur,
                    generation_type=gen_type,
                    reference_image_ids=[img_ref.image_id],
                    visual_description=(
                        f"Cinematic {aspect_ratio} shot framing {img_ref.original_filename} ({', '.join(img_ref.subjects)}) "
                        f"in a richly lit {occasion.lower()} environment."
                    ),
                    scene_objective=f"Celebrate chapter {scene_num} of this unforgettable {occasion.lower()} journey.",
                    camera_movement=cam_movement,
                    motion_prompt=(
                        f"Cinematic {aspect_ratio} motion: {cam_movement}. "
                        f"Subjects: {', '.join(img_ref.subjects)}. Environment: {img_ref.environment}. "
                        f"Atmosphere: {img_ref.mood}. Ultra-smooth Wan 2.2 realistic video synthesis."
                    ),
                    transition="dissolve" if i > 0 else "fade",
                    narration_text=narration,
                )
            )

        used_ids = set()
        for sc in scenes:
            for rid in sc.reference_image_ids:
                used_ids.add(str(rid))
        all_ids = {str(a.image_id) for a in analyses}
        omitted_ids = sorted(list(all_ids - used_ids))

        unselected_explanation = None
        if omitted_ids:
            unselected_explanation = (
                f"Reference image(s) {', '.join(omitted_ids)} were omitted to maintain a focused "
                f"{num_scenes}-scene narrative arc strictly matching the requested {duration}s duration."
            )

        opening = f"Welcome to a heartfelt celebration of this special {occasion.lower()}."
        closing = f"May the joy and beauty of this {occasion.lower()} remain cherished forever."
        full_script = f"{opening} {' '.join(full_narration_lines)} {closing}"

        return ScenePlan(
            video_concept=f"{occasion} Celebration - {duration}s Cinematic Journey",
            occasion=occasion,
            story_summary=(
                f"A heartfelt and cinematic {duration}-second tribute celebrating {occasion.lower()} "
                f"through cherished visual moments, gentle movement, and inspiring narration."
            ),
            visual_style="Warm golden tones, soft bokeh depth of field, 4k ultra-high definition cinematic grading",
            emotional_tone=f"Uplifting, nostalgic, and joyous {occasion.lower()} celebration",
            total_duration=duration,
            aspect_ratio=aspect_ratio,
            full_narration_script=full_script,
            opening_narration=opening,
            closing_narration=closing,
            music_mood=music_mood or ("Celebration" if occasion.lower() in ("birthday", "celebration") else "Cinematic"),
            background_music_prompt=(
                custom_music_prompt or f"Inspiring acoustic orchestral soundtrack with gentle piano and soaring strings for {occasion.lower()}"
            ),
            unselected_images_explanation=unselected_explanation,
            scenes=scenes,
        )
