import hashlib
import re
import unicodedata


_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]+")
_LIMITED_FORM = re.compile(
    r"\b(?:LTD|LIMITED)\s+(?:STI|SIRKETI)\b"
)
_ANONIM_FORM = re.compile(
    r"\b(?:A\s*S|ANONIM\s+SIRKETI)\b"
)
_BRANCH_AFTER_LEGAL_FORM = re.compile(
    r"\b(?P<legal_form>"
    r"LIMITED SIRKETI|ANONIM SIRKETI"
    r")\s+.+\s+SUBESI$"
)


def canonical_company_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Company name must be text.")

    cleaned = " ".join(
        unicodedata.normalize("NFKC", name).split()
    )

    if not cleaned:
        raise ValueError("Company name cannot be blank.")

    if any(
        unicodedata.category(character) in {"Cc", "Cf"}
        for character in cleaned
    ):
        raise ValueError(
            "Company name contains invalid control characters."
        )

    normalized = cleaned.replace("ı", "i").replace("İ", "I")
    normalized = unicodedata.normalize(
        "NFKD",
        normalized,
    ).upper()
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = _NON_ALPHANUMERIC.sub(
        " ",
        normalized,
    )
    normalized = " ".join(normalized.split())

    normalized = _LIMITED_FORM.sub(
        "LIMITED SIRKETI",
        normalized,
    )
    normalized = _ANONIM_FORM.sub(
        "ANONIM SIRKETI",
        normalized,
    )

    normalized = _BRANCH_AFTER_LEGAL_FORM.sub(
        r"\g<legal_form>",
        normalized,
    )

    return normalized


def company_name_key(name: str) -> str:
    canonical_name = canonical_company_name(name)

    return hashlib.sha256(
        canonical_name.encode("utf-8")
    ).hexdigest()
