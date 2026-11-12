"""Normalize provider usage without inventing unknown token counts."""


def normalize_usage(value):
    if not isinstance(value, dict):
        return None
    inputs, outputs = value.get("input_tokens"), value.get("output_tokens")
    details = value.get("input_tokens_details") or {}
    if not isinstance(details, dict):
        return None
    cached = value.get("cached_input_tokens", details.get("cached_tokens", 0))
    if (
        any(type(v) is not int or v < 0 for v in (inputs, outputs, cached))
        or cached > inputs
    ):
        return None
    return {
        "input_tokens": inputs,
        "output_tokens": outputs,
        "cached_input_tokens": cached,
        "total_tokens": inputs + outputs,
    }
