"""Coverage checks for LLM-selected verbatim spans."""


def require_non_whitespace_coverage(
    source_text: str,
    spans: list[tuple[int, int]],
    *,
    label: str,
) -> None:
    cursor = 0
    for start, end in spans:
        if source_text[cursor:start].strip():
            raise ValueError(f"{label} omit non-whitespace source text at {cursor}:{start}")
        cursor = end
    if source_text[cursor:].strip():
        raise ValueError(f"{label} omit non-whitespace source text at {cursor}:{len(source_text)}")
