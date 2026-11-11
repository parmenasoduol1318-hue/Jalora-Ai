"""Comparison and ranking engine (Phase 3).

Provides algorithms for comparing items, ranking options, and
making data-driven comparisons.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.errors import ToolResult

logger = logging.getLogger("jalora.comparison_engine")


class ComparisonDimension(str, Enum):
    """Dimensions along which items can be compared."""
    PRICE = "price"
    PERFORMANCE = "performance"
    QUALITY = "quality"
    SPEED = "speed"
    RELIABILITY = "reliability"
    FEATURES = "features"
    USABILITY = "usability"
    SECURITY = "security"
    COMPATIBILITY = "compatibility"
    DURABILITY = "durability"
    SUPPORT = "support"
    CUSTOM = "custom"  # Custom-defined dimension


class ComparisonOperator(str, Enum):
    """Operators for comparison operations."""
    GREATER = ">"
    LESS = "<"
    EQUAL = "="
    GREATER_EQUAL = ">="
    LESS_EQUAL = "<="
    NOT_EQUAL = "!="


@dataclass
class ComparisonCriterion:
    """A criterion for comparing items."""
    name: str
    weight: float = 1.0  # 0.0-1.0, importance in ranking
    operator: ComparisonOperator = ComparisonOperator.GREATER
    better_is: str = "higher"  # "higher" or "lower"
    description: str = ""


@dataclass
class ItemScore:
    """Score for an item on a criterion."""
    item_id: str
    item_name: str
    dimension: str
    score: float  # 0.0-1.0
    raw_value: Any = None
    unit: str = ""
    
    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "item_name": self.item_name,
            "dimension": self.dimension,
            "score": self.score,
            "raw_value": str(self.raw_value) if self.raw_value else None,
            "unit": self.unit,
        }


@dataclass
class Item:
    """An item being compared."""
    id: str
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComparisonResult:
    """Result of comparing a set of items."""
    items: list[Item]
    criteria: list[ComparisonCriterion]
    scores: list[list[ItemScore]]  # [item][criterion]
    ranked: list[str]  # Item IDs ranked (best first)
    winner: str | None = None
    tie_groups: list[list[str]] = field(default_factory=list)
    comparison_matrix: dict[str, dict[str, any]] = field(default_factory=dict)
    
    def get_ranked_items(self) -> list[tuple[str, float]]:
        """Get items ranked by weighted score."""
        item_scores = {}
        for item_scores_row in self.scores:
            for score in item_scores_row:
                if score.item_id not in item_scores:
                    item_scores[score.item_id] = 0.0
                item_scores[score.item_id] += score.score * score.dimension
        
        return sorted(item_scores.items(), key=lambda x: x[1], reverse=True)


class ComparisonEngine:
    """Engine for comparing and ranking items."""
    
    def __init__(self, default_weight: float = 1.0):
        """Initialize the comparison engine."""
        self.default_weight = default_weight
        self._results_cache: dict[str, ComparisonResult] = {}
    
    def compare_items(
        self,
        items: list[Item],
        criteria: list[ComparisonCriterion] | None = None,
        normalize: bool = True
    ) -> ComparisonResult:
        """Compare multiple items against criteria.
        
        Args:
            items: Items to compare
            criteria: Criteria for comparison (defaults to available attributes)
            normalize: Whether to normalize scores to 0-1 range
            
        Returns:
            ComparisonResult with rankings
        """
        if not items:
            return ComparisonResult(items=[], criteria=[], scores=[], ranked=[])
        
        # Auto-generate criteria from item attributes if not provided
        if not criteria:
            criteria = self._auto_generate_criteria(items)
        
        # Score each item against each criterion
        all_scores = []
        for item in items:
            item_scores = []
            for criterion in criteria:
                score = self._score_item(item, criterion, normalize)
                item_scores.append(score)
            all_scores.append(item_scores)
        
        # Rank items by weighted score
        ranked = self._rank_items(items, criteria, all_scores)
        
        # Find winner and ties
        winner = ranked[0] if ranked else None
        tie_groups = self._find_ties(all_scores, ranked)
        
        # Build comparison matrix
        comparison_matrix = self._build_comparison_matrix(items, criteria, all_scores)
        
        return ComparisonResult(
            items=items,
            criteria=criteria,
            scores=all_scores,
            ranked=ranked,
            winner=winner,
            tie_groups=tie_groups,
            comparison_matrix=comparison_matrix,
        )
    
    def _auto_generate_criteria(self, items: list[Item]) -> list[ComparisonCriterion]:
        """Generate comparison criteria from item attributes."""
        # Find common attributes
        all_keys = set()
        for item in items:
            all_keys.update(item.attributes.keys())
        
        criteria = []
        for key in sorted(all_keys):
            # Determine if higher is better based on attribute name
            better_is_higher = not any(
                bad in key.lower() for bad in ["cost", "expense", "error", "fail", "time"]
            )
            
            criteria.append(ComparisonCriterion(
                name=key,
                weight=self.default_weight,
                better_is="higher" if better_is_higher else "lower",
                description=f"Comparison criterion: {key}"
            ))
        
        return criteria
    
    def _score_item(self, item: Item, criterion: ComparisonCriterion, normalize: bool) -> ItemScore:
        """Score an item against a criterion."""
        attr_name = criterion.name
        raw_value = item.attributes.get(attr_name)
        
        if raw_value is None:
            return ItemScore(
                item_id=item.id,
                item_name=item.name,
                dimension=attr_name,
                score=0.5,  # Neutral score for missing data
                raw_value=None,
            )
        
        # Normalize to 0-1 range
        if normalize and isinstance(raw_value, (int, float)):
            # Use min-max normalization (simplified - assumes 0-100 scale for demo)
            score = max(0.0, min(1.0, float(raw_value) / 100.0))
            
            # Adjust if lower is better
            if criterion.better_is == "lower":
                score = 1.0 - score
            
            return ItemScore(
                item_id=item.id,
                item_name=item.name,
                dimension=attr_name,
                score=score,
                raw_value=raw_value,
            )
        
        # For non-numeric values, use a simple matching score
        score = 0.5
        
        return ItemScore(
            item_id=item.id,
            item_name=item.name,
            dimension=attr_name,
            score=score,
            raw_value=raw_value,
        )
    
    def _rank_items(self, items: list[Item], criteria: list[ComparisonCriterion], 
                   all_scores: list[list[ItemScore]]) -> list[str]:
        """Rank items by weighted score."""
        weighted_scores = {}
        
        for item_idx, item in enumerate(items):
            total = 0.0
            for criterion_idx, criterion in enumerate(criteria):
                score = all_scores[item_idx][criterion_idx]
                total += score.score * criterion.weight
            weighted_scores[item.id] = total
        
        return sorted(weighted_scores.keys(), key=lambda x: weighted_scores[x], reverse=True)
    
    def _find_ties(self, all_scores: list[list[ItemScore]], ranked: list[str]) -> list[list[str]]:
        """Find items that are tied in ranking."""
        if len(ranked) < 2:
            return []
        
        # Get top scores
        scores = [
            (item_id, sum(s.score for s in scores if s.item_id == item_id))
            for item_id in ranked
        ]
        
        ties = []
        current_tie = [scores[0][0]]
        
        for i in range(1, len(scores)):
            if abs(scores[i][1] - scores[i-1][1]) < 0.01:  # Threshold for tie
                current_tie.append(scores[i][0])
            else:
                if len(current_tie) > 1:
                    ties.append(current_tie)
                current_tie = [scores[i][0]]
        
        if len(current_tie) > 1:
            ties.append(current_tie)
        
        return ties
    
    def _build_comparison_matrix(
        self, 
        items: list[Item], 
        criteria: list[ComparisonCriterion], 
        all_scores: list[list[ItemScore]]
    ) -> dict[str, dict[str, any]]:
        """Build a comparison matrix showing pairwise comparisons."""
        matrix = {}
        
        for i, item1 in enumerate(items):
            matrix[item1.id] = {}
            for j, item2 in enumerate(items):
                if i == j:
                    matrix[item1.id][item2.id] = 0  # Same item
                else:
                    # Calculate pairwise score difference
                    diff = 0
                    total = 0
                    for k, criterion in enumerate(criteria):
                        s1 = all_scores[i][k].score
                        s2 = all_scores[j][k].score
                        diff += (s1 - s2) * criterion.weight
                        total += criterion.weight
                    
                    matrix[item1.id][item2.id] = round(diff / total if total > 0 else 0, 3)
        
        return matrix
    
    def generate_report(self, result: ComparisonResult) -> str:
        """Generate a human-readable comparison report."""
        lines = ["# Comparison Report", ""]
        
        if result.winner:
            winner_item = next((i for i in result.items if i.id == result.winner), None)
            if winner_item:
                lines.append(f"**Winner:** {winner_item.name}")
                lines.append("")
        
        lines.append("## Rankings")
        for rank, item_id in enumerate(result.ranked, 1):
            item = next((i for i in result.items if i.id == item_id), None)
            if item:
                lines.append(f"{rank}. {item.name}")
        
        if result.tie_groups:
            lines.append("")
            lines.append("## Ties")
            for tie in result.tie_groups:
                tied_names = [i.name for i in result.items if i.id in tie]
                lines.append(f"  - {', '.join(tied_names)}")
        
        return "\n".join(lines)


# Singleton instance
_engine: ComparisonEngine | None = None


def get_comparison_engine() -> ComparisonEngine:
    """Get or create the comparison engine singleton."""
    global _engine
    if _engine is None:
        _engine = ComparisonEngine()
    return _engine


async def compare_items(items_data: list[dict], criteria: list[str] | None = None) -> ToolResult:
    """Compare multiple items and rank them.
    
    Args:
        items_data: List of item dictionaries with 'id', 'name', and 'attributes'
        criteria: Optional list of criteria names to use
        
    Returns:
        ToolResult with comparison result
    """
    items = [
        Item(
            id=i.get("id", ""),
            name=i.get("name", ""),
            attributes=i.get("attributes", {}),
        )
        for i in items_data
    ]
    
    engine = get_comparison_engine()
    result = engine.compare_items(items)
    
    ranked_items = [
        {
            "id": next((i.id for i in result.items if i.id == item_id), item_id),
            "name": next((i.name for i in result.items if i.id == item_id), item_id),
            "rank": idx + 1,
        }
        for idx, item_id in enumerate(result.ranked)
    ]
    
    return ToolResult.ok(
        f"Compared {len(items)} items, winner: {result.ranked[0] if result.ranked else 'none'}",
        {
            "winner_id": result.winner,
            "rankings": ranked_items,
            "tie_groups_count": len(result.tie_groups),
        }
    )


async def score_item(item: dict, criteria: list[dict]) -> ToolResult:
    """Score a single item against given criteria."""
    engine = get_comparison_engine()
    
    # Create temporary item
    temp_item = Item(
        id=item.get("id", "temp"),
        name=item.get("name", ""),
        attributes=item.get("attributes", {}),
    )
    
    temp_criteria = [
        ComparisonCriterion(
            name=c.get("name", ""),
            weight=c.get("weight", 1.0),
            better_is=c.get("better_is", "higher"),
        )
        for c in criteria
    ]
    
    result = engine.compare_items([temp_item], temp_criteria)
    
    if result.scores and result.scores[0]:
        total_score = sum(s.score * s.dimension for s in result.scores[0])
        
        return ToolResult.ok(
            f"Item scored: {total_score:.2f}",
            {
                "item_id": temp_item.id,
                "item_name": temp_item.name,
                "total_score": round(total_score, 3),
                "dimension_scores": {s.dimension: round(s.score, 3) for s in result.scores[0]},
            }
        )
    
    return ToolResult.fail("Could not score item", "No valid criteria provided")