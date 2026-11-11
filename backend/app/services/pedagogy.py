"""Pedagogical levels + deterministic, offline explanations (Phase 5).

The math/science pipeline computes an authoritative :class:`MathResult`; this
module governs *how that result is explained* across the depth ladder
``primary -> highschool -> university -> stem -> postgrad -> professor``.

Two responsibilities:

1. **Level resolution** — :func:`normalize_level` canonicalises a configured or
   requested level, :func:`infer_level` reads a level from the question text
   ("explain like I'm 5", "for a professor"), and :func:`resolve_level` combines
   them (explicit config/arg wins, else the question, else ``auto``).

2. **Deterministic derivation rendering** — :func:`render` produces a
   level-aware explanation *without any model*, so the "show the working"
   guarantee (spec item 9) still adapts to the reader. The verified answer block
   from :func:`math_engine.format_reply` is always preserved verbatim; only a
   level-appropriate derivation header is prepended.

The per-level model *directives* live here too (:func:`model_directive`), so the
explanation layer (:mod:`app.services.science_explain`) and the offline renderer
share one definition of each level.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import math_engine

# Lowest -> highest depth. ``auto`` is not a member; it means "pick for me".
LEVEL_ORDER: tuple[str, ...] = (
    "primary", "highschool", "university", "stem", "postgrad", "professor",
)
AUTO = "auto"


@dataclass(frozen=True)
class LevelSpec:
    key: str
    label: str
    rank: int
    directive: str       # instruction handed to the model when it writes the prose
    header: str          # derivation header used by the offline (no-model) render


LEVELS: dict[str, LevelSpec] = {
    "primary": LevelSpec(
        "primary", "Primary school", 0,
        "Explain for a primary-school pupil (ages 8-11): very simple words, tiny "
        "steps, one real-life example, no jargon.",
        "**Let's do this step by step:**",
    ),
    "highschool": LevelSpec(
        "highschool", "High school", 1,
        "Explain for a high-school student: clear steps, and name the "
        "rules/formulas you use.",
        "**Working:**",
    ),
    "university": LevelSpec(
        "university", "University", 2,
        "Explain for a university student: precise terminology and the full "
        "derivation of each step.",
        "**Working:**",
    ),
    "stem": LevelSpec(
        "stem", "STEM undergraduate", 3,
        "Explain for a STEM undergraduate: rigorous, naming the theorem or "
        "identity behind each step.",
        "**Working:**",
    ),
    "postgrad": LevelSpec(
        "postgrad", "Postgraduate", 4,
        "Explain for a postgraduate: formal and concise, noting assumptions and "
        "edge cases.",
        "**Working (concise):**",
    ),
    "professor": LevelSpec(
        "professor", "Professor", 5,
        "Address a professor: terse and formal, cite the relevant "
        "theorem/identity without hand-holding.",
        "",
    ),
}

# The generic directive used when no specific level is chosen.
_AUTO_DIRECTIVE = "Explain clearly at a level suitable for the question."

# Aliases -> canonical level key (lower-cased, hyphens/underscores as spaces).
_ALIASES: dict[str, str] = {
    "primary": "primary", "elementary": "primary", "elementry": "primary",
    "basic": "primary", "kid": "primary", "kids": "primary", "child": "primary",
    "children": "primary", "beginner": "primary", "simple": "primary",
    "highschool": "highschool", "high school": "highschool",
    "highschool level": "highschool", "secondary": "highschool",
    "secondary school": "highschool", "school": "highschool",
    "teen": "highschool", "teenager": "highschool",
    "university": "university", "college": "university", "degree": "university",
    "undergrad": "stem", "undergraduate": "stem", "bsc": "stem", "b.sc": "stem",
    "stem": "stem", "engineering": "stem",
    "postgrad": "postgrad", "postgraduate": "postgrad", "grad": "postgrad",
    "graduate": "postgrad", "master": "postgrad", "masters": "postgrad",
    "msc": "postgrad", "m.sc": "postgrad", "phd": "postgrad",
    "doctoral": "postgrad", "doctorate": "postgrad",
    "professor": "professor", "prof": "professor", "expert": "professor",
    "research": "professor",
}

# Inference cues, evaluated highest-rank first so an explicit request for rigour
# is never watered down by an incidental phrase. Each entry: (regex, canonical).
_INFER_CUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bprof(?:essor)?\b|\bexpert\b|\brigorous(?:ly)?\b|\bformal(?:ly)?\b"
                r"|\bno hand.?holding\b|\bterse\b|\bphd[- ]level\b", re.IGNORECASE), "professor"),
    (re.compile(r"\bpost ?grad(?:uate)?\b|\bmaster'?s?\b|\bm\.?sc\b|\bgraduate "
                r"(?:level|student|school)\b|\bdoctoral\b|\bdoctorate\b", re.IGNORECASE), "postgrad"),
    (re.compile(r"\bundergrad(?:uate)?\b|\bstem\b|\bb\.?sc\b|\bengineering "
                r"(?:student|level|major)\b", re.IGNORECASE), "stem"),
    (re.compile(r"\buniversity\b|\bcollege\b|\bdegree[- ]level\b", re.IGNORECASE), "university"),
    (re.compile(r"\bhigh ?school\b|\bsecondary school\b|\bteen(?:ager)?\b"
                r"|\bschool[- ]level\b", re.IGNORECASE), "highschool"),
    (re.compile(r"\blike i'?m (?:5|five|a (?:kid|child))\b|\beli5\b"
                r"|\bfor (?:a )?(?:kid|child|children|primary|elementary)\b"
                r"|\bprimary school\b|\belementary (?:school|level)?\b"
                r"|\bin simple (?:words|terms|language)\b|\bsimply put\b"
                r"|\beasy (?:words|terms|language)\b|\bexplain simply\b"
                r"|\bbeginner(?:'s)?\b", re.IGNORECASE), "primary"),
)


def _norm_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", " ", value.strip().lower()).strip()


def normalize_level(value: str | None) -> str:
    """Canonicalise ``value`` to a level key, or ``"auto"`` if unrecognised."""
    if not value:
        return AUTO
    key = _norm_key(str(value))
    if key in ("", "auto", "default", "any"):
        return AUTO
    if key in LEVELS:
        return key
    aliased = _ALIASES.get(key)
    if aliased:
        return aliased
    # tolerate "high-school", "post-graduate", "primary school" style inputs
    compact = key.replace(" ", "")
    if compact in LEVELS:
        return compact
    return _ALIASES.get(compact, AUTO)


def infer_level(text: str | None) -> str:
    """Read a target level from the question text, or ``"auto"`` if none."""
    if not text:
        return AUTO
    for pattern, level in _INFER_CUES:
        if pattern.search(text):
            return level
    return AUTO


def resolve_level(value: str | None = None, question: str | None = None) -> str:
    """Resolve the effective level: explicit config/arg wins, else the question."""
    explicit = normalize_level(value)
    if explicit != AUTO:
        return explicit
    return infer_level(question)


def model_directive(level: str | None) -> str:
    """The instruction handed to the model for writing the explanation prose."""
    key = normalize_level(level)
    if key == AUTO:
        return _AUTO_DIRECTIVE
    return LEVELS[key].directive


def render(result: math_engine.MathResult, level: str | None = None,
           question: str | None = None, show_working: bool = True) -> str:
    """Deterministic, level-aware explanation of ``result`` (no model needed).

    The verified answer block from :func:`math_engine.format_reply` is always
    preserved; a level-appropriate derivation header is prepended for a concrete
    level. ``auto`` (or an uncomputed result) falls back to the plain block, so
    the default output is unchanged.

    When ``show_working`` is False, only the final answer line is returned
    (:func:`math_engine.format_answer`) — no steps, no level header.
    """
    if not show_working:
        return math_engine.format_answer(result)
    block = math_engine.format_reply(result)
    if not result.ok or result.error:
        return block
    key = resolve_level(level, question)
    if key == AUTO:
        return block
    header = LEVELS[key].header
    if not header:
        return block
    return f"{header}\n\n{block}"
