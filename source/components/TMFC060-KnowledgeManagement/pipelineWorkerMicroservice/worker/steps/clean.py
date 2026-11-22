"""Text cleaning: Unicode whitespace and control characters."""
import re
import unicodedata

CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​﻿]")
SPACES = re.compile(r"[ \t  -   　]+")


def clean(text: str) -> str:
    """Normalise to NFC, drop control and zero-width characters, collapse runs of spaces, trim each line."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = CONTROL.sub("", text)
    lines = [SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()
