"""Vision routing: native multimodal content or auxiliary image descriptions."""

from agent.vision.attach import (
    build_multimodal_content,
    build_tool_message_content,
    build_user_provider_content,
    describe_images_into_text,
    extract_text_from_content,
    prune_image_parts,
)
from agent.vision.capabilities import (
    VisionMode,
    decide_vision_mode,
    model_looks_vision_capable,
)
from agent.vision.encode import (
    image_url_part,
    load_image_as_data_url,
    screenshots_dir,
)

__all__ = [
    "VisionMode",
    "build_multimodal_content",
    "build_tool_message_content",
    "build_user_provider_content",
    "decide_vision_mode",
    "describe_images_into_text",
    "extract_text_from_content",
    "image_url_part",
    "load_image_as_data_url",
    "model_looks_vision_capable",
    "prune_image_parts",
    "screenshots_dir",
]
