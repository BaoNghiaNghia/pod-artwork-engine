from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError


ModelT = TypeVar("ModelT", bound=BaseModel)


class TypedBoundaryError(ValueError):
    pass


def _extract_json_text(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\\s*```$", "", text)
        text = text.strip()

    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if not starts:
        raise TypedBoundaryError("structured payload does not contain JSON")
    start = min(starts)
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise TypedBoundaryError("structured payload contains incomplete JSON")
    candidate = text[start : end + 1]
    try:
        json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise TypedBoundaryError(f"structured payload is invalid JSON: {exc}") from exc
    return candidate


def validate_typed_payload(model: type[ModelT], payload: str | bytes | dict[str, Any]) -> ModelT:
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            payload = json.loads(_extract_json_text(payload))
        return model.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise TypedBoundaryError(f"{model.__name__} validation failed: {exc}") from exc
