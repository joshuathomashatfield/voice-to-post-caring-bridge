"""
Privacy scanning utilities.

Runs lightweight, deterministic pattern checks (no LLM call needed) over
user-provided text and generated drafts to flag potentially sensitive
details before they end up in a shareable post -- addresses, phone numbers,
IDs, etc. This is a heuristic safety net, not a guarantee; the user is
always shown the flagged text and decides what to do with it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class PrivacyFlag:
    category: str
    matched_text: str
    message: str


_PATTERNS = [
    ("phone_number", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
     "This looks like a phone number."),
    ("ssn_like", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
     "This looks like a Social Security Number."),
    ("credit_card_like", re.compile(r"\b(?:\d[ -]?){13,16}\b"),
     "This looks like it could be a card or account number."),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
     "This looks like an email address."),
    ("street_address", re.compile(
        r"\b\d{1,6}\s+([A-Za-z0-9'.\-]+\s){1,4}"
        r"(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Circle|Cir)\b",
        re.IGNORECASE),
     "This looks like a street address."),
    ("exact_birthdate", re.compile(
        r"\b(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])[/\-](?:19|20)\d{2}\b"),
     "This looks like a full date that could be a birth date."),
    ("patient_id_like", re.compile(r"\b(?:MRN|Patient\s?ID|Account\s?#)\s*[:#]?\s*\w+\b", re.IGNORECASE),
     "This looks like a patient or account ID."),
]


def scan_text(text: str) -> List[PrivacyFlag]:
    if not text:
        return []
    flags: List[PrivacyFlag] = []
    for category, pattern, message in _PATTERNS:
        for match in pattern.finditer(text):
            flags.append(PrivacyFlag(category=category, matched_text=match.group(0), message=message))
    return flags


def has_high_risk_flags(flags: List[PrivacyFlag]) -> bool:
    high_risk = {"ssn_like", "credit_card_like", "patient_id_like"}
    return any(f.category in high_risk for f in flags)
