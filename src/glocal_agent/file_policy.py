"""Conservative credential exclusions shared by scanning and extraction."""

import re
from pathlib import Path

TEXT_EXTENSIONS = frozenset({".txt", ".md", ".csv", ".tsv", ".sh"})
HTML_EXTENSIONS = frozenset({".html", ".htm"})
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | HTML_EXTENSIONS | {
    ".pdf", ".docx", ".xlsx", ".xlsm", ".pptx",
}


def sensitive_filename(filename: str) -> bool:
    name = Path(filename).name.lower()
    return (
        Path(name).suffix in {".pem", ".key", ".p12", ".pfx", ".keystore"}
        or name in {".env", "id_rsa", "id_ed25519", "id_ecdsa", "credentials"}
        or bool(re.search(r"(?:^|[_.-])(?:credentials?|secrets?)(?:[_.-]|$)", name))
    )


_CREDENTIAL_MARKERS = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
    r"|\b(?:aws[_ ]secret[_ ]access[_ ]key|secret[_ ]?access[_ ]?key|smtp[_ ]password)\b"
    r"|\b(?:api[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*[\"']?[^\s\"']{12,}"
    r"|\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b",
    re.IGNORECASE,
)


def credential_content(text: str) -> bool:
    return bool(_CREDENTIAL_MARKERS.search(text))
