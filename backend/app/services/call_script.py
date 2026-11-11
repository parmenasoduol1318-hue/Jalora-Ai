"""Structured call scripts (§33): a conditional question flow the agent can
conduct over the phone.

A script is a list of nodes. Each node has an identifier, the line Jalora
says aloud, an optional `branch` map deciding which node comes next based on
the person's answer, and an optional `answer_key` slot where the collected
answer is recorded. `branch` keys are plain-language labels ("yes", "no",
"approved", "rejected", "when", ...); they are matched with simple keyword
classification so both DTMF digits and spoken replies drive the same flow.

The default script is deterministic (used by the offline mock provider and as
the fallback when the agent supplies no explicit questions).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class ScriptError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class ScriptNode:
    id: str
    speak: str
    expect: str = ""                              # what the person is asked, e.g. "say 'yes' or 'no'"
    branch: dict[str, str] = field(default_factory=dict)  # answer label -> next node id
    answer_key: str | None = None                 # slot to store the answer under
    final: bool = False                           # end the call after this reply


@dataclass
class CallScript:
    nodes: list[ScriptNode]
    first: str
    purpose: str = ""

    def node(self, node_id: str) -> ScriptNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def as_dict(self) -> list[dict[str, Any]]:
        return [
            {
                "id": n.id, "speak": n.speak, "expect": n.expect,
                "branch": n.branch, "answer_key": n.answer_key, "final": n.final,
            }
            for n in self.nodes
        ]


# ---------------------------------------------------------------------------
# deterministic default script
# ---------------------------------------------------------------------------

def build_script(purpose: str, questions: list[str] | None = None) -> CallScript:
    """Default deterministic script from a purpose + optional follow-ups.

    Flow: greeting + approval ask -> conditional follow-ups -> closing.
    `questions` are appended as branch-driven asks reached when the person
    answers "yes" to the approval ask (§33: "if approved, ask ...").
    """
    questions = list(questions or [])
    ask_nodes: list[ScriptNode] = []
    for i, question in enumerate(questions):
        last = i == len(questions) - 1
        ask_nodes.append(
            ScriptNode(
                id=f"ask_{i + 1}",
                speak=f"One more thing: {question}",
                expect="Please tell me your answer.",
                branch={"yes": "closing", "no": "closing",
                        "done": "closing", "unknown": "closing"} if last else {
                    "yes": f"ask_{i + 2}", "no": f"ask_{i + 2}",
                    "done": "closing", "unknown": "closing",
                },
                answer_key=f"q{i + 1}",
            )
        )

    nodes: list[ScriptNode] = [
        ScriptNode(
            id="intro",
            speak=(
                f"Hello, this is an automated call from Jalora. I'm calling about: "
                f"{purpose}. Are you happy to proceed?"
            ),
            expect="Please say 'yes' or 'no'.",
            branch={**({"yes": ask_nodes[0].id} if ask_nodes else {"yes": "closing"}),
                    "no": "closing", "rejected": "closing",
                    "done": "closing", "unknown": "closing"},
            answer_key="approved",
        ),
        *ask_nodes,
        ScriptNode(id="closing", speak="Thank you. Goodbye.", final=True),
    ]
    return CallScript(nodes=nodes, first="intro", purpose=purpose)


def validate_script(script: CallScript) -> list[str]:
    """Return structural problems: missing targets and unreachable cycles."""
    problems: list[str] = []
    ids = {n.id for n in script.nodes}
    if script.first not in ids:
        problems.append(f"first node '{script.first}' is not in the script")
    for node in script.nodes:
        for label, target in node.branch.items():
            if target not in ids:
                problems.append(
                    f"node '{node.id}' branch '{label}' targets missing '{target}'"
                )
    return problems


# ---------------------------------------------------------------------------
# answer classification → branch resolution
# ---------------------------------------------------------------------------

_YES_WORDS = ("yes", "sure", "okay", "ok", "fine", "approved", "accept")
_NO_WORDS = ("no", "cancel", "stop", "rejected", "decline")
_DONE_WORDS = ("done", "bye", "goodbye", "thanks", "thank you", "nothing", "nope")


def classify_answer(text: str) -> str:
    """Map a spoken/DTMF reply to a branch label: yes|no|done|unknown.

    Digit keys first (1=yes, 2=no, 0=done), then word tokens; "no thanks"
    reads as no (decline), a bare "thanks" reads as done.
    """
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    words = re.findall(r"[a-z0-9]+", t)
    if not words:
        return "unknown"
    if words == ["1"]:
        return "yes"
    if words == ["2"]:
        return "no"
    if words == ["0"]:
        return "done"
    if words in (["y"], ["n"]):
        return "yes" if words == ["y"] else "no"
    for w in _NO_WORDS:
        if w in t:
            return "no"
    for w in _DONE_WORDS:
        if w in t:
            return "done"
    for w in _YES_WORDS:
        if w in t:
            return "yes"
    return "unknown"


def advance_script(script: CallScript, node: ScriptNode, answer: str) -> ScriptNode | None:
    """Next node after the current `node` given `answer`. Returns None when
    the call is finished (final node or a terminal branch like closing/press)."""
    if node.final:
        return None
    label = classify_answer(answer)
    target = node.branch.get(label)
    if target is None:
        # fall back to any branch key that matched the answer meaning
        target = node.branch.get("unknown")
    if target is None:
        return None
    nxt = script.node(target)
    return nxt


# ---------------------------------------------------------------------------
# convenience matching helpers used by tool/service tests
# ---------------------------------------------------------------------------

def matches(label: str, answer: str) -> bool:
    """True when `answer` classifies to `label` (exact or aliased)."""
    return classify_answer(answer) == label