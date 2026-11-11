"""Ranking and comparison tools (Phase 3).

Tools for ranking options, comparing alternatives, and making
data-driven decisions.
"""
from __future__ import annotations

from typing import Any

from app.context import get_tool_context
from app.errors import ToolResult
from app.services.comparison_engine import (
    get_comparison_engine,
    Item,
    ComparisonCriterion,
    ComparisonOperator,
)
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Ranking Tools
# ============================================================================

async def rank_options(
    options: list[dict],
    weights: dict[str, float] | None = None,
    asc_order: dict[str, bool] | None = None
) -> ToolResult:
    """Rank a list of options based on weighted criteria.
    
    Args:
        options: List of option dictionaries with 'id', 'name', and 'score' or 'attributes'
        weights: Optional dict mapping attribute names to weights (0.0-1.0)
        asc_order: Optional dict mapping attribute names to sort order (True=ascending)
        
    Returns:
        ToolResult with ranked options
    """
    if not options:
        return ToolResult.fail("No options provided", "Please provide at least one option")
    
    engine = get_comparison_engine()
    
    # Convert options to Item objects
    items = []
    for opt in options:
        if "id" not in opt or "name" not in opt:
            return ToolResult.fail("Invalid option", "Each option must have 'id' and 'name'")
        
        # Handle both 'score' (single number) and 'attributes' (multiple criteria)
        if "score" in opt:
            attributes = {"overall_score": opt["score"]}
        elif "attributes" in opt:
            attributes = opt["attributes"]
        else:
            attributes = {}
        
        items.append(Item(id=opt["id"], name=opt["name"], attributes=attributes))
    
    # Generate criteria from weights or use available attributes
    criteria = []
    if weights:
        for attr_name, weight in weights.items():
            better_is = "lower" if asc_order and asc_order.get(attr_name, False) else "higher"
            criteria.append(ComparisonCriterion(
                name=attr_name,
                weight=weight,
                better_is=better_is,
            ))
    else:
        # Auto-generate criteria
        criteria = engine._auto_generate_criteria(items) if hasattr(engine, '_auto_generate_criteria') else []
    
    result = engine.compare_items(items, criteria or criteria)
    
    # Build ranked result
    ranked = []
    for rank, item_id in enumerate(result.ranked, 1):
        item = next((i for i in result.items if i.id == item_id), None)
        if item:
            ranked.append({
                "id": item.id,
                "name": item.name,
                "rank": rank,
                "score": sum(
                    s.score * s.dimension 
                    for s in result.scores 
                    if isinstance(s, ItemScore) 
                    for _ in [1] 
                    if s.item_id == item_id
                ) if result.scores else 0.5,
            })
    
    return ToolResult.ok(
        f"Ranked {len(options)} options",
        {"ranked": ranked, "winner_id": result.winner}
    )


async def pairwise_compare(
    option_a: dict,
    option_b: dict,
    criteria: dict[str, dict]
) -> ToolResult:
    """Compare two options against specific criteria.
    
    Args:
        option_a: First option with 'id', 'name', 'attributes'
        option_b: Second option with 'id', 'name', 'attributes'
        criteria: Dict of criterion name -> {weight, better_is}
        
    Returns:
        ToolResult with comparison outcome
    """
    items = [
        Item(id=option_a.get("id", ""), name=option_a.get("name", ""), attributes=option_a.get("attributes", {})),
        Item(id=option_b.get("id", ""), name=option_b.get("name", ""), attributes=option_b.get("attributes", {}))
    ]
    
    criterion_objs = [
        ComparisonCriterion(
            name=name,
            weight=data.get("weight", 1.0),
            better_is=data.get("better_is", "higher"),
        )
        for name, data in criteria.items()
    ]
    
    engine = get_comparison_engine()
    result = engine.compare_items(items, criterion_objs)
    
    if result.ranked and len(result.ranked) >= 2:
        winner_id = result.ranked[0]
        loser_id = result.ranked[1]
        margin = abs(result.comparison_matrix.get(winner_id, {}).get(loser_id, 0))
        
        return ToolResult.ok(
            f"Winner: {next((i.name for i in items if i.id == winner_id), winner_id)}",
            {
                "winner_id": winner_id,
                "loser_id": loser_id,
                "margin": round(margin, 3),
                "is_clear_winner": margin > 0.3,
            }
        )
    
    return ToolResult.ok("Options are equivalent", {"winner_id": None, "loser_id": None, "margin": 0})


async def score_option(
    option: dict,
    criteria_scores: list[dict]
) -> ToolResult:
    """Score an option against multiple criteria.
    
    Args:
        option: Option with 'id', 'name', 'attributes'
        criteria_scores: List of {criterion, score, weight}
        
    Returns:
        ToolResult with aggregate score
    """
    if not criteria_scores:
        return ToolResult.fail("No criteria provided", "Please provide at least one criterion")
    
    total_weight = sum(c.get("weight", 1.0) for c in criteria_scores)
    weighted_sum = sum(
        c.get("score", 0.5) * c.get("weight", 1.0)
        for c in criteria_scores
    )
    
    aggregate_score = weighted_sum / total_weight if total_weight > 0 else 0.5
    
    return ToolResult.ok(
        f"Option scored: {aggregate_score:.2f}",
        {
            "option_id": option.get("id"),
            "option_name": option.get("name"),
            "aggregate_score": round(aggregate_score, 4),
            "criteria_breakdown": criteria_scores,
        }
    )


async def find_top_k(
    options: list[dict],
    k: int = 3,
    weights: dict[str, float] | None = None
) -> ToolResult:
    """Find the top K options from a list.
    
    Args:
        options: List of option dictionaries
        k: Number of top options to return
        weights: Optional weights for criteria
        
    Returns:
        ToolResult with top K options
    """
    result = await rank_options(options, weights)
    
    if result.is_ok:
        ranked = result.data.get("ranked", [])
        top_k = ranked[:k]
        return ToolResult.ok(
            f"Found top {k} options",
            {"top_options": top_k, "count": len(top_k)}
        )
    
    return ToolResult.fail("Ranking failed", "Could not rank options")


async def recommend_option(
    options: list[dict],
    preferences: dict[str, Any] | None = None
) -> ToolResult:
    """Recommend the best option based on user preferences.
    
    Args:
        options: List of options to choose from
        preferences: User preferences like {max_price: 100, required_features: ["a", "b"]}
        
    Returns:
        ToolResult with recommendation
    """
    if not options:
        return ToolResult.fail("No options provided", "Please provide at least one option")
    
    # Filter by hard constraints
    filtered = options
    
    if preferences:
        # Filter by max price
        if "max_price" in preferences:
            filtered = [
                o for o in filtered
                if o.get("attributes", {}).get("price", float("inf")) <= preferences["max_price"]
            ]
        
        # Filter by required features
        if "required_features" in preferences:
            required = preferences["required_features"]
            filtered = [
                o for o in filtered
                if any(f in str(o.get("attributes", {}).get("features", [])).lower() for f in required)
            ]
    
    if not filtered:
        return ToolResult.fail("No matching options", "No options match the specified constraints")
    
    # Rank filtered options
    result = await rank_options(filtered, preferences.get("weights"))
    
    if result.is_ok and result.data.get("winner_id"):
        winner_id = result.data["winner_id"]
        winner = next((o for o in filtered if o.get("id") == winner_id), None)
        
        return ToolResult.ok(
            f"Recommended: {winner.get('name', winner_id) if winner else winner_id}",
            {
                "recommendation": winner_id,
                "confidence": "high" if len(filtered) == 1 else "medium",
                "options_considered": len(filtered),
            }
        )
    
    return ToolResult.fail("Recommendation failed", "Could not determine best option")


# ============================================================================
# Tool Registration
# ============================================================================

# Import ItemScore for type hints
from app.services.comparison_engine import ItemScore

registry.register(
    rank_options,
    permission=PermissionLevel.AUTO,
    category="ranking",
    tags=["rank", "sort", "options"]
)

registry.register(
    pairwise_compare,
    permission=PermissionLevel.AUTO,
    category="ranking",
    tags=["compare", "pairs", "alternatives"]
)

registry.register(
    score_option,
    permission=PermissionLevel.AUTO,
    category="ranking",
    tags=["score", "evaluate"]
)

registry.register(
    find_top_k,
    permission=PermissionLevel.AUTO,
    category="ranking",
    tags=["top", "k-best", "recommendation"]
)

registry.register(
    recommend_option,
    permission=PermissionLevel.AUTO,
    category="ranking",
    tags=["recommend", "choose", "decision"]
)