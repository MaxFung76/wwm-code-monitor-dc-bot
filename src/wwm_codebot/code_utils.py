from __future__ import annotations

import re


CODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{6,24}(?![A-Za-z0-9])")


def is_probable_code(token: str) -> bool:
    return any(ch.isalpha() for ch in token)


def normalize_code(token: str) -> str:
    return token.strip().upper()


def extract_codes_from_text(text: str) -> list[str]:
    tokens = CODE_PATTERN.findall(text.replace("\xa0", " "))
    seen: set[str] = set()
    codes: list[str] = []
    for token in tokens:
        normalized = normalize_code(token)
        if not is_probable_code(normalized):
            continue
        if normalized not in seen:
            seen.add(normalized)
            codes.append(normalized)
    return codes

