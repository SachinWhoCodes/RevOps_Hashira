"""Remove personal identifiers from text before it is sent to an AI model (data minimisation).

Masked: email addresses, phone-like and card-like digit runs (10-19 digits, separators allowed),
Indian PAN and Aadhaar-style numbers. Kept: short numbers, amounts and dates, which the
explanations need ("add 15 seats", "₹2,137,000", "2026-09-16").
"""

from __future__ import annotations

import re

EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
DIGIT_RUN = re.compile(r"(?<![\w])\+?\(?\d[\d\s().-]{8,24}\d(?![\w])")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def _mask_digits(match: re.Match[str]) -> str:
    text = match.group(0)
    digits = sum(ch.isdigit() for ch in text)
    if 10 <= digits <= 19 and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text.strip()):
        return "[number]"
    return text


def mask(text: str) -> str:
    text = EMAIL.sub("[email]", text)
    text = PAN.sub("[id]", text)
    return DIGIT_RUN.sub(_mask_digits, text)
