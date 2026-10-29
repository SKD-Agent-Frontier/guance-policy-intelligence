"""Privacy-safe classification of organize model output parse failures.

Only fixed reason codes leave this module; raw model output is never logged.
Classification does not change parser acceptance or evidence verification.
"""
import json
import re


def parse_failure_reason(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return "empty_output"

    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    start = cleaned.find("{")
    if start < 0:
        return "root_not_object" if cleaned.lstrip().startswith("[") else "no_json_object"

    # The production parser rejects array/wrapper punctuation before its object.
    if any(ch in cleaned[:start] for ch in "}[]"):
        return "unexpected_wrapper"

    fragment = cleaned[start:]
    try:
        value, used = json.JSONDecoder().raw_decode(fragment)
    except json.JSONDecodeError as exc:
        if "Unterminated string" in exc.msg or exc.pos >= len(fragment) - 1:
            return "incomplete_json"
        return "invalid_json"

    tail = fragment[used:]
    if "{" in tail:
        return "multiple_objects"
    if any(ch in tail for ch in "}[]"):
        return "unexpected_wrapper"
    if not isinstance(value, dict):
        return "root_not_object"
    if "policies" not in value:
        return "missing_policies"
    if not isinstance(value["policies"], list):
        return "invalid_policies"
    return "other_parse_failure"
