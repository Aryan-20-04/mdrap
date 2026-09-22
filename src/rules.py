"""MDRAP User Rule Registry and Decorator.

Supports user-defined quality rules allocated to bitmask range 32..63.
Evaluated in Python post-native pass with zero overhead when no rules are registered.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any
from models import CanonicalEvent, QualityStatus

USER_BIT_MIN = 32
USER_BIT_MAX = 63

@dataclass(slots=True)
class UserRule:
    bit: int
    name: str
    description: str
    severity: QualityStatus
    func: Callable[[CanonicalEvent], bool]


_USER_RULES: dict[int, UserRule] = {}
_USER_RULE_NAMES: set[str] = set()


def register_rule(
    bit: int,
    name: str,
    description: str = "",
    severity: QualityStatus = QualityStatus.SUSPICIOUS,
):
    """Decorator to register a user-defined quality rule in bitmask range 32..63."""
    if not (USER_BIT_MIN <= bit <= USER_BIT_MAX):
        raise ValueError(
            f"User rule bit {bit} out of range [{USER_BIT_MIN}, {USER_BIT_MAX}]. "
            "Bits 0..15 are core platform rules, 16..31 are reserved."
        )

    clean_name = name.strip().upper()

    def decorator(fn: Callable[[CanonicalEvent], bool]):
        if bit in _USER_RULES:
            raise ValueError(f"Rule bit {bit} is already registered to '{_USER_RULES[bit].name}'")
        if clean_name in _USER_RULE_NAMES:
            raise ValueError(f"Rule name '{clean_name}' is already registered")

        rule = UserRule(
            bit=bit,
            name=clean_name,
            description=description,
            severity=severity,
            func=fn,
        )
        _USER_RULES[bit] = rule
        _USER_RULE_NAMES.add(clean_name)
        return fn

    return decorator


def unregister_rule(bit: int) -> None:
    """Unregister a user-defined rule by bit."""
    rule = _USER_RULES.pop(bit, None)
    if rule:
        _USER_RULE_NAMES.discard(rule.name)


def clear_user_rules() -> None:
    """Clear all registered user rules (useful in test teardown)."""
    _USER_RULES.clear()
    _USER_RULE_NAMES.clear()


def get_user_rules() -> list[UserRule]:
    """Get all currently registered user rules."""
    return list(_USER_RULES.values())


def evaluate_user_rules(event: CanonicalEvent) -> CanonicalEvent:
    """Evaluate registered user rules against CanonicalEvent post-native pass."""
    if not _USER_RULES:
        return event

    for rule in _USER_RULES.values():
        try:
            violation = rule.func(event)
            if violation:
                # Monotonic status escalation
                if rule.severity == QualityStatus.INVALID:
                    event.quality_status = QualityStatus.INVALID
                elif rule.severity == QualityStatus.SUSPICIOUS and event.quality_status != QualityStatus.INVALID:
                    event.quality_status = QualityStatus.SUSPICIOUS

                if rule.name not in event.reasons:
                    event.reasons.append(rule.name)
        except Exception:
            # Bad user rule never crashes the pipeline; mark schema violation
            if "USER_RULE_ERROR" not in event.reasons:
                event.reasons.append("USER_RULE_ERROR")

    return event
