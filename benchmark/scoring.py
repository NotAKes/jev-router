"""Offline strict scoring and conservative, single-wrapper normalization."""

import json
import re

SCORING_VERSION = "strict-v1+outer-wrapper-v1"

__all__ = ["SCORING_VERSION", "strict_equal", "grade", "normalized_text", "normalized_grade"]


def strict_equal(left, right):
    """Compare JSON-like values recursively without numeric type coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(strict_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def grade(text, answer):
    """Preserve the original runner's exact/JSON scoring semantics."""
    if answer["type"] == "exact":
        return text.strip() in answer["accepted"]
    try:
        return strict_equal(json.loads(text), answer["value"])
    except (ValueError, TypeError):
        return False


def normalized_text(text: str) -> str:
    """Trim whitespace and remove at most one complete outer Markdown wrapper.

    Fences must occupy separate lines, with an empty, json, or text info
    string. Nested/multiple fences, partial wrappers and prose are left alone.
    No repeated normalization or extraction from the wrapper's content occurs.
    """
    text = text.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json|text)?\r?\n(.*?)\r?\n```", text, re.DOTALL)
        if match is not None and "```" not in match.group(1):
            return match.group(1).strip()
        return text
    if text.startswith("**") and text.endswith("**") and len(text) >= 4:
        inner = text[2:-2]
        if "**" not in inner:
            return inner.strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        inner = text[1:-1]
        if not any(char in inner for char in "`\r\n"):
            return inner.strip()
    return text


def normalized_grade(text, answer):
    """Apply strict grading after one conservative normalization pass."""
    return grade(normalized_text(text), answer)
