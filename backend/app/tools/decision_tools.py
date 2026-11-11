"""Decision support tools (Phase 3).

Tools for making decisions with multiple criteria and options.
"""
from __future__ import annotations

from typing import Any

from app.context import get_tool_context
from app.errors import ToolResult
from app.services.decision_support import (
    get_decision_engine,
    DecisionType,
    DecisionCriterion,
    DecisionOption,
    DecisionContext,
)
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Decision Tools
# ============================================================================

async def make_decision(
    decision_id: str,
    options: list[dict],
    criteria: list[dict] | None = None,
    context: dict | None = None
) -> ToolResult:
    """Make a decision from multiple options using specified criteria.
    
    Args:
        decision_id: Unique identifier for this decision
        options: List of options with id, name, attributes, etc.
        criteria: Optional list of criteria (name, weight, better_is)
        context: Optional context (type, urgency, stakeholders, etc.)
        
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
    decision_criteria = []
    if criteria:
        for c in criteria:
            decision_criteria.append(DecisionCriterion(
                name=c.get("name", ""),
                weight=c.get("weight", 1.0),
                description=c.get("description", ""),
            ))
    
    # Build context
    decision_context = DecisionContext(
        decision_type=DecisionType(context.get("type", "operational") if context else "operational"),
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
        
        selected_option = next(
            (o for o in decision_options if o.id == result.selected),
            None
        )
        
        return ToolResult.ok(
            f"Decision recommended: {selected_option.name if selected_option else result.selected}",
            result.to_summary()
        )
    except Exception as e:
        import logging
        logging.getLogger("jalora.decision_tools").exception("Decision failed")
        return ToolResult.fail("Decision analysis failed", str(e))


async def get_recommendations(
    decision_type: str,
    factors: dict[str, Any]
) -> ToolResult:
    """Get recommendations for a decision type based on factors.
    
    Args:
        decision_type: Type of decision (strategic, tactical, operational, emergency, routine)
        factors: Key factors to consider
        
    Returns:
        ToolResult with recommendations
    """
    try:
        dtype = DecisionType(decision_type.lower())
    except ValueError:
        dtype = DecisionType.OPERATIONAL
    
    recommendations = []
    
    # Type-specific recommendations
    if dtype == DecisionType.STRATEGIC:
        recommendations.extend([
            "Consider long-term impact (3-5 years)",
            "Engage executive stakeholders early",
            "Evaluate competitive implications",
            "Assess resource allocation trade-offs",
        ])
    elif dtype == DecisionType.TACTICAL:
        recommendations.extend([
            "Focus on operational efficiency",
            "Consider immediate resource constraints",
            "Evaluate team readiness",
            "Plan for implementation timeline",
        ])
    elif dtype == DecisionType.EMERGENCY:
        recommendations.extend([
            "Prioritize speed over perfection",
            "Activate emergency protocols",
            "Communicate decision rationale quickly",
            "Document action for post-mortem",
        ])
    else:  # OPERATIONAL, ROUTINE
        recommendations.extend([
            "Follow established procedures",
            "Check available resources",
            "Minimize disruption to workflow",
        ])
    
    # Factor-specific recommendations
    if "urgency" in factors:
        if factors["urgency"] in ("critical", "high"):
            recommendations.append("Urgency is critical - prioritize expedited decision")
    
    if "budget" in factors:
        if factors["budget"] and factors["budget"] < 10000:
            recommendations.append("Budget constrained - consider cost-effective options")
    
    if "stakeholders" in factors:
        recs_count = len(factors["stakeholders"]) if factors["stakeholders"] else 0
        if recs_count > 2:
            recommendations.append(f"{recs_count} stakeholders involved - ensure alignment")
    
    return ToolResult.ok(
        f"Generated {len(recommendations)} recommendations for {decision_type} decision",
        {
            "decision_type": decision_type,
            "recommendations": recommendations,
        }
    )


async def analyze_tradeoffs(
    options: list[dict],
    tradeoff_dimensions: list[str]
) -> ToolResult:
    """Analyze trade-offs between options across dimensions.
    
    Args:
        options: List of options to compare
        tradeoff_dimensions: Dimensions to analyze (cost, time, quality, etc.)
        
    Returns:
        ToolResult with trade-off analysis
    """
    if len(options) < 2:
        return ToolResult.fail("Need at least 2 options", "Trade-off analysis requires multiple options")
    
    analysis = {}
    
    for dim in tradeoff_dimensions:
        # Get values for each option
        values = {}
        for opt in options:
            val = opt.get("attributes", {}).get(dim) or opt.get(dim)
            values[opt.get("id", opt.get("name", "unknown"))] = val if isinstance(val, (int, float)) else 0
        
        # Find best/potential compromise
        sorted_options = sorted(values.items(), key=lambda x: x[1], reverse=True)
        best = sorted_options[0][0] if sorted_options else None
        worst = sorted_options[-1][0] if sorted_options else None
        
        analysis[dim] = {
            "best_option": best,
            "best_value": sorted_options[0][1] if sorted_options else 0,
            "worst_option": worst,
            "worst_value": sorted_options[-1][1] if sorted_options else 0,
            "range": sorted_options[0][1] - sorted_options[-1][1] if len(sorted_options) > 1 else 0,
        }
    
    return ToolResult.ok(
        f"Analyzed {len(tradeoff_dimensions)} trade-off dimensions",
        {"dimension_analysis": analysis}
    )


async def simulate_outcome(
    action: dict,
    assumptions: dict[str, Any] | None = None
) -> ToolResult:
    """Simulate potential outcome of a decision/action.
    
    Args:
        action: Action description and parameters
        assumptions: Optional assumptions to consider
        
    Returns:
        ToolResult with simulation results
    """
    # Simple Monte Carlo-style simulation placeholder
    # In production, this would use actual models
    
    action_type = action.get("type", "generic")
    params = action.get("parameters", {})
    
    # Simulated outcomes
    positive_outcomes = [
        "Achieved primary objective",
        "Improved efficiency",
        "Positive stakeholder response",
        "Cost savings realized",
    ]
    
    negative_outcomes = [
        "Partial failure",
        "Resource overruns",
        "Stakeholder resistance",
        "Timeline delays",
    ]
    
    # Risk assessment
    base_risk = 0.1
    if action_type == "strategic":
        base_risk = 0.3
    elif action_type == "emergency":
        base_risk = 0.4
    
    # Adjust based on parameters
    if params.get("complexity"):
        base_risk += 0.1 * params.get("complexity", 1)
    if params.get("stakeholders"):
        base_risk += 0.05 * len(params.get("stakeholders", []))
    
    risk_level = "low" if base_risk < 0.2 else "medium" if base_risk < 0.4 else "high"
    
    expected_positive = len(positive_outcomes) * 0.7
    expected_negative = len(negative_outcomes) * 0.3
    
    return ToolResult.ok(
        f"Simulated outcome (risk: {risk_level})",
        {
            "action_type": action_type,
            "expected_positive_outcomes": positive_outcomes[:expected_positive],
            "expected_negative_outcomes": negative_outcomes[:expected_negative],
            "risk_level": risk_level,
            "risk_score": round(base_risk, 2),
        }
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(
    make_decision,
    permission=PermissionLevel.CONFIRM,  # Decisions may need approval
    category="decision",
    tags=["decision", "choose", "option"]
)

registry.register(
    get_recommendations,
    permission=PermissionLevel.AUTO,
    category="decision",
    tags=["recommend", "advice"]
)

registry.register(
    analyze_tradeoffs,
    permission=PermissionLevel.AUTO,
    category="decision",
    tags=["tradeoff", "analyze", "compare"]
)

registry.register(
    simulate_outcome,
    permission=PermissionLevel.NOTIFY,  # Inform user of risks
    category="decision",
    tags=["simulate", "risk", "outcome"]
)