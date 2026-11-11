"""Decision support system (Phase 3).

Provides frameworks and tools for making complex decisions
with multiple factors and criteria.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.errors import ToolResult

logger = logging.getLogger("jalora.decision_support")


class DecisionType(str, Enum):
    """Types of decisions."""
    STRATEGIC = "strategic"      # Long-term, high-impact
    TACTICAL = "tactical"        # Mid-term, operational
    OPERATIONAL = "operational"  # Short-term, routine
    EMERGENCY = "emergency"     # Crisis, time-sensitive
    ROUTINE = "routine"          # Standard procedures


class DecisionStatus(str, Enum):
    """Status of a decision process."""
    PROPOSED = "proposed"        # Decision identified
    ANALYZED = "analyzed"        # Criteria evaluated
    RANKED = "ranked"            # Options ranked
    SELECTED = "selected"        # Choice made
    IMPLEMENTED = "implemented"  # Action taken
    REJECTED = "rejected"        # Decision rejected
    CANCELLED = "cancelled"      # Process cancelled


@dataclass
class DecisionCriterion:
    """A criterion used in decision-making."""
    name: str
    weight: float = 1.0  # 0.0-1.0
    description: str = ""
    measurable: bool = True
    unit: str = ""


@dataclass
class DecisionOption:
    """An option in a decision process."""
    id: str
    name: str
    description: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)  # criterion -> score
    risks: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    cost: float | None = None
    time_required: float | None = None  # in hours


@dataclass
class DecisionContext:
    """Context for a decision."""
    decision_type: DecisionType
    urgency: str = "medium"  # low, medium, high, critical
    stakeholders: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    resources: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionResult:
    """Result of a decision analysis."""
    decision_id: str
    options: list[DecisionOption]
    criteria: list[DecisionCriterion]
    context: DecisionContext
    scores: dict[str, float]  # option_id -> total score
    rank: list[str]  # option IDs ranked
    selected: str | None = None
    confidence: float = 0.5
    recommendations: list[str] = field(default_factory=list)
    risks_noted: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_summary(self) -> dict:
        """Generate a summary of the decision."""
        return {
            "decision_id": self.decision_id,
            "selected_option": self.selected,
            "confidence": self.confidence,
            "ranked_options": [
                {
                    "id": opt_id,
                    "name": next((o.name for o in self.options if o.id == opt_id), "Unknown"),
                    "score": self.scores.get(opt_id, 0),
                    "rank": idx + 1,
                }
                for idx, opt_id in enumerate(self.rank)
            ],
            "recommendations": self.recommendations,
        }


class DecisionSupportEngine:
    """Engine for supporting complex decisions."""
    
    def __init__(self):
        """Initialize the decision support engine."""
        self._decisions: dict[str, DecisionResult] = {}
    
    def analyze_decision(
        self,
        decision_id: str,
        options: list[DecisionOption],
        criteria: list[DecisionCriterion],
        context: DecisionContext
    ) -> DecisionResult:
        """Analyze a decision with given options, criteria, and context.
        
        Args:
            decision_id: Unique identifier for this decision
            options: Available options
            criteria: Evaluation criteria
            context: Decision context
            
        Returns:
            DecisionResult with rankings and recommendations
        """
        if not options:
            raise ValueError("Must provide at least one option")
        
        if not criteria:
            # Auto-generate criteria from option attributes
            criteria = self._auto_generate_criteria(options)
        
        # Calculate scores for each option
        scores = self._calculate_scores(options, criteria)
        
        # Rank options
        ranked = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        
        # Select best option
        selected = ranked[0] if ranked else None
        
        # Calculate confidence based on score differences
        confidence = self._calculate_confidence(scores, ranked)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            options, criteria, context, selected
        )
        
        # Identify risks
        risks = self._identify_risks(options, selected)
        
        result = DecisionResult(
            decision_id=decision_id,
            options=options,
            criteria=criteria,
            context=context,
            scores=scores,
            rank=ranked,
            selected=selected,
            confidence=confidence,
            recommendations=recommendations,
            risks_noted=risks,
        )
        
        self._decisions[decision_id] = result
        return result
    
    def _auto_generate_criteria(self, options: list[DecisionOption]) -> list[DecisionCriterion]:
        """Auto-generate evaluation criteria from options."""
        all_attrs = set()
        for opt in options:
            all_attrs.update(opt.attributes.keys())
        
        criteria = []
        for attr in sorted(all_attrs):
            # Skip non-numerical attributes
            has_numeric = any(
                isinstance(opt.attributes.get(attr), (int, float))
                for opt in options
            )
            if has_numeric:
                criteria.append(DecisionCriterion(
                    name=attr,
                    weight=1.0,
                    description=f"Criterion: {attr}",
                ))
        
        if not criteria:
            # Default criteria
            criteria = [
                DecisionCriterion(name="cost", weight=1.0, description="Cost effectiveness"),
                DecisionCriterion(name="benefit", weight=1.0, description="Benefit"),
            ]
        
        return criteria
    
    def _calculate_scores(
        self,
        options: list[DecisionOption],
        criteria: list[DecisionCriterion]
    ) -> dict[str, float]:
        """Calculate scores for each option."""
        scores = {}
        
        for option in options:
            total = 0.0
            total_weight = 0.0
            
            for criterion in criteria:
                attr_value = option.attributes.get(criterion.name)
                
                if attr_value is not None and isinstance(attr_value, (int, float)):
                    # Normalize score (assume 0-100 scale)
                    normalized = max(0.0, min(1.0, float(attr_value) / 100.0))
                    total += normalized * criterion.weight
                
                total_weight += criterion.weight
            
            scores[option.id] = total / total_weight if total_weight > 0 else 0.5
        
        return scores
    
    def _calculate_confidence(self, scores: dict[str, float], ranked: list[str]) -> float:
        """Calculate confidence in the top-ranked option."""
        if len(ranked) < 2:
            return 0.8  # High confidence for single option
        
        # Confidence based on score difference
        top_score = scores.get(ranked[0], 0)
        second_score = scores.get(ranked[1], 0)
        
        diff = top_score - second_score
        
        # Map difference to confidence (0-1)
        if diff > 0.3:
            return 0.9
        elif diff > 0.15:
            return 0.7
        elif diff > 0.05:
            return 0.5
        else:
            return 0.3
    
    def _generate_recommendations(
        self,
        options: list[DecisionOption],
        criteria: list[DecisionCriterion],
        context: DecisionContext,
        selected: str | None
    ) -> list[str]:
        """Generate decision recommendations."""
        recommendations = []
        
        if not selected:
            recommendations.append("No clear winner - consider additional criteria")
            return recommendations
        
        selected_option = next((o for o in options if o.id == selected), None)
        if not selected_option:
            return recommendations
        
        # General recommendation
        recommendations.append(f"Recommend proceeding with option: {selected_option.name}")
        
        # Check for cost considerations
        if selected_option.cost is not None:
            if context.decision_type == DecisionType.EMERGENCY:
                recommendations.append("In emergency situation, speed prioritized over cost")
            elif selected_option.cost > 10000:
                recommendations.append("High cost option - consider alternatives if budget constrained")
        
        # Check for time considerations
        if selected_option.time_required is not None:
            if context.urgency == "critical" and selected_option.time_required > 24:
                recommendations.append("Time required exceeds urgency - consider alternatives")
        
        # Check stakeholders
        if "executive" in [s.lower() for s in context.stakeholders]:
            recommendations.append("Executive stakeholder identified - ensure alignment")
        
        return recommendations
    
    def _identify_risks(self, options: list[DecisionOption], selected: str | None) -> list[str]:
        """Identify risks associated with the selected option."""
        risks = []
        
        if selected:
            option = next((o for o in options if o.id == selected), None)
            if option:
                risks.extend(option.risks[:3])  # First 3 risks
        
        return risks


# Singleton instance
_engine: DecisionSupportEngine | None = None


def get_decision_engine() -> DecisionSupportEngine:
    """Get or create the decision engine singleton."""
    global _engine
    if _engine is None:
        _engine = DecisionSupportEngine()
    return _engine


async def evaluate_options(
    decision_id: str,
    options: list[dict],
    criteria: list[dict] | None = None,
    context: dict | None = None
) -> ToolResult:
    """Evaluate options and recommend the best choice.
    
    Args:
        decision_id: Unique decision identifier
        options: List of option dictionaries
        criteria: Optional list of criteria (name, weight, description)
        context: Optional decision context
        
    Returns:
        ToolResult with decision recommendation
    """
    engine = get_decision_engine()
    
    # Convert options
    decision_options = [
        DecisionOption(
            id=o.get("id", ""),
            name=o.get("name", ""),
            description=o.get("description", ""),
            attributes=o.get("attributes", {}),
            risks=o.get("risks", []),
            benefits=o.get("benefits", []),
            cost=o.get("cost"),
            time_required=o.get("time_required"),
        )
        for o in options
    ]
    
    # Convert criteria
    decision_criteria = [
        DecisionCriterion(
            name=c.get("name", ""),
            weight=c.get("weight", 1.0),
            description=c.get("description", ""),
        )
        for c in (criteria or [])
    ]
    
    # Convert context
    decision_context = DecisionContext(
        decision_type=DecisionType(c.get("type", "operational")),
        urgency=context.get("urgency", "medium") if context else "medium",
        stakeholders=context.get("stakeholders", []) if context else [],
        constraints=context.get("constraints", []) if context else [],
        resources=context.get("resources", {}) if context else {},
    )
    
    try:
        result = engine.analyze_decision(
            decision_id=decision_id,
            options=decision_options,
            criteria=decision_criteria,
            context=decision_context,
        )
        
        summary = result.to_summary()
        
        return ToolResult.ok(
            f"Decision '{decision_id}' analyzed. Recommended: {result.selected}",
            summary
        )
    except Exception as e:
        logger.exception("Decision evaluation failed")
        return ToolResult.fail("Decision analysis failed", str(e))


async def compare_decisions(
    decision_a_id: str,
    decision_b_id: str
) -> ToolResult:
    """Compare two previously analyzed decisions.
    
    Args:
        decision_a_id: First decision ID
        decision_b_id: Second decision ID
        
    Returns:
        ToolResult with comparison
    """
    engine = get_decision_engine()
    
    result_a = engine._decisions.get(decision_a_id)
    result_b = engine._decisions.get(decision_b_id)
    
    if not result_a or not result_b:
        return ToolResult.fail(
            "Not found",
            "One or both decisions not found in history"
        )
    
    score_a = result_a.scores.get(result_a.rank[0], 0) if result_a.rank else 0
    score_b = result_b.scores.get(result_b.rank[0], 0) if result_b.rank else 0
    
    return ToolResult.ok(
        "Decisions compared",
        {
            "decision_a": {
                "id": decision_a_id,
                "winner": result_a.selected,
                "score": score_a,
            },
            "decision_b": {
                "id": decision_b_id,
                "winner": result_b.selected,
                "score": score_b,
            },
            "comparison": {
                "better": decision_a_id if score_a > score_b else decision_b_id,
                "score_difference": abs(score_a - score_b),
            },
        }
    )