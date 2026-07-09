from __future__ import annotations

import csv
from typing import Any, Dict, Tuple


class FrontmatterError(ValueError):
    """Raised when a Markdown file has malformed frontmatter."""


def parse_markdown_with_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break

    if end_index is None:
        raise FrontmatterError("frontmatter starts with '---' but has no closing delimiter")

    metadata = parse_simple_yaml("\n".join(lines[1:end_index]))
    body = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    return metadata, body


def parse_simple_yaml(yaml_text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    current_list_key = None

    for line_number, raw_line in enumerate(yaml_text.splitlines(), start=1):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- "):
            if current_list_key is None:
                raise FrontmatterError(f"line {line_number}: list item has no key")
            data[current_list_key].append(_parse_scalar(stripped[2:].strip()))
            continue

        if ":" not in line:
            raise FrontmatterError(f"line {line_number}: expected 'key: value'")

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise FrontmatterError(f"line {line_number}: empty key")

        if value == "":
            data[key] = []
            current_list_key = key
        elif value.startswith("[") and value.endswith("]"):
            data[key] = _parse_inline_list(value)
            current_list_key = None
        else:
            data[key] = _parse_scalar(value)
            current_list_key = None

    return data


def _parse_inline_list(value: str) -> Any:
    inner = value[1:-1].strip()
    if not inner:
        return []
    reader = csv.reader([inner], skipinitialspace=True)
    return [_parse_scalar(item.strip()) for item in next(reader)]


def _parse_scalar(value: str) -> Any:
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value
