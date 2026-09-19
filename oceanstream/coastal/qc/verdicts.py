"""Product-specific scientific admissibility; never confuse it with execution."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def verdict(flags: Iterable[str], **evidence: Any) -> dict[str, Any]:
    reasons = sorted(set(flags))
    return {
        "passed": not reasons,
        "flags": reasons,
        "summary": "; ".join(reasons) if reasons else "All required product checks passed.",
        "evidence": evidence,
    }


def combine(verdicts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    flags = []
    if not verdicts:
        flags.append("no_products_assessed")
    for key, item in verdicts.items():
        reasons = item.get("flags", [])
        if item.get("passed") is not True and not reasons:
            reasons = ["validity_unverified"]
        flags.extend(f"{key}:{reason}" for reason in reasons)
    return verdict(flags, products=verdicts)
