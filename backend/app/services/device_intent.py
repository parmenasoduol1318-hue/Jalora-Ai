"""Small deterministic intent helpers for app launches on paired devices."""
from __future__ import annotations

import re
from dataclasses import dataclass

_APP_LAUNCH = re.compile(
    r"^\s*(?:(?:hey\s+)?jalora[, ]+)?(?:please\s+)?"
    r"(?:open|launch|start|use)\s+(?P<target>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_FOLLOW_UP = re.compile(
    r"(?:,\s*|\s+(?:and\s+then|and|then)\s+|\s+to\s+|;\s*)"
    r"(?=(?:please\s+)?(?:tell|check|read|summari[sz]e|search|find|"
    r"look|inspect|open|click|tap|type|scroll|reply|send|post|"
    r"browse|navigate|review|mark|delete|pay|prepare|fill|select|play|"
    r"watch|translate|use)\b)",
    re.IGNORECASE,
)
_APP_PREFIX = re.compile(r"^(?:(?:my|the)\s+)+", re.IGNORECASE)
_APP_SUFFIX = re.compile(r"\s+(?:app|application)$", re.IGNORECASE)
_DEVICE_SUFFIX = re.compile(
    r"\s+(?:on|in|using|through)\s+"
    r"(?:(?:my|the|paired|connected|remote)\s+)*"
    r"(?:(?:android|mobile)\s+)?"
    r"(?:phone|tablet|device|android|iphone|ipad|ios)\s*$",
    re.IGNORECASE,
)
_INSTRUCTION_PREFIX = re.compile(
    r"^\s*(?:tell\s+me|who|what|check|read|summari[sz]e|search|find|"
    r"look|inspect|open|launch|start|send|reply|scroll|click|tap|type)\b",
    re.IGNORECASE,
)
_ALIASES = {
    "my email": "Gmail",
    "email": "Gmail",
    "email app": "Gmail",
    "my browser": "Chrome",
    "browser": "Chrome",
}
_NON_APP_TARGETS = frozenset(
    {"any", "any app", "any application", "all apps", "an app", "app", "application"}
)
_MOBILE_APP_NAMES = frozenset(
    {"whatsapp", "instagram", "facebook", "gmail", "chrome"}
)


@dataclass(frozen=True)
class AppLaunchRequest:
    app_name: str
    follow_up: str = ""


def parse_app_launch_request(value: str) -> AppLaunchRequest | None:
    """Extract the app and any following task from a natural-language launch request."""
    match = _APP_LAUNCH.fullmatch(value or "")
    if match is None:
        return None

    target = match.group("target").strip()
    follow_up = ""
    clause = _FOLLOW_UP.search(target)
    if clause is not None:
        follow_up = target[clause.end():].strip(" \t\r\n.!?,;")
        target = target[:clause.start()].strip(" \t\r\n.!?,;")

    target = _APP_PREFIX.sub("", target)
    target = _DEVICE_SUFFIX.sub("", target).strip(" \t\r\n.!?,;")
    target = _APP_SUFFIX.sub("", target).strip(" \t\r\n.!?,;")
    if not target or len(target) > 120 or any(
        ord(character) < 32 or ord(character) == 127 for character in target
    ):
        return None
    if target.casefold() in _NON_APP_TARGETS:
        return None

    canonical = _ALIASES.get(target.casefold())
    return AppLaunchRequest(canonical or target, follow_up)


def resolve_app_launch_target(value: str) -> AppLaunchRequest | None:
    """Normalize a tool's app-name argument, tolerating a model-supplied full request."""
    parsed = parse_app_launch_request(value)
    if parsed is not None:
        return parsed

    if _INSTRUCTION_PREFIX.search(value or ""):
        return None

    parsed = parse_app_launch_request(f"open {value or ''}")
    if parsed is not None:
        return parsed

    return None


def is_common_mobile_app_name(value: str) -> bool:
    """Recognize common phone app aliases without restricting generic app names."""
    normalized = _APP_PREFIX.sub("", (value or "").strip())
    normalized = _APP_SUFFIX.sub("", normalized).strip()
    canonical = _ALIASES.get(normalized.casefold(), normalized)
    return canonical.casefold() in _MOBILE_APP_NAMES
