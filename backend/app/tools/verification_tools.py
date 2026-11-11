"""Fact verification and comparison tools (spec §§3, 14).

Tools for verifying claims, comparing items, and decision support.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry
from app.services.verification import (
    get_verification_engine,
    get_comparison_ranker,
    get_decision_engine,
)


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Fact Verification Tools
# ============================================================================

async def verify_claim(claim: str, max_sources: int = 3) -> ToolResult:
    """Verify a factual claim using multiple sources.
    
    Args:
        claim: The claim to verify
        max_sources: Maximum sources to search
        
    Returns:
        ToolResult with verification result
    """
    engine = get_verification_engine()
    
    result = engine.verify_claim(claim)
    
    return ToolResult.ok(
        f"Claim verification: {'VERIFIED' if result.verified else 'UNVERIFIED'}",
        {
            "claim": result.claim,
            "verified": result.verified,
            "confidence": round(result.confidence, 3),
            "evidence_count": len(result.evidence),
            "summary": result.summary,
            "key_points": result.key_points,
            "contradictions": result.contradictions
        }
    )


async def check_source_credibility(url: str) -> ToolResult:
    """Check the credibility of a source.
    
    Args:
        url: URL to check
        
    Returns:
        ToolResult with credibility assessment
    """
    domain = url.split("//")[-1].split("/")[0] if "//" in url else url
    
    credibility_map = {
        ".gov": 0.95,
        ".edu": 0.90,
        ".org": 0.85,
        "wikipedia": 0.95,
        "medium.com": 0.6,
        "blogspot": 0.5
    }
    
    score = 0.5  # Default
    for key, value in credibility_map.items():
        if key in domain.lower():
            score = value
            break
    
    return ToolResult.ok(
        f"Credibility assessment for {domain}",
        {
            "url": url,
            "domain": domain,
            "credibility_score": score,
            "assessment": "high" if score > 0.8 else "medium" if score > 0.6 else "low"
        }
    )


# ============================================================================
# Comparison and Ranking Tools
# ============================================================================

async def compare_items(items: list[str], 
                       criteria: dict[str, float] | None = None) -> ToolResult:
    """Compare items and generate a ranked recommendation.
    
    Args:
        items: List of items to compare
        criteria: Weight for each criterion {criterion: weight}
        
    Returns:
        ToolResult with comparison ranking
    """
    ranker = get_comparison_ranker()
    
    result = ranker.compare_items(items, criteria)
    
    return ToolResult.ok(
        f"Compared {len(items)} items",
        {
            "items": result.items,
            "criteria": result.criteria,
            "scores": result.scores,
            "ranking": result.ranking,
            "recommended": result.recommended,
            "rationale": result.rationale
        }
    )


async def rank_project_management_tools() -> ToolResult:
    """Get a ranking of popular project management tools.
    
    Returns:
        ToolResult with tool rankings
    """
    items = ["Asana", "Trello", "Monday.com", "ClickUp", "Notion", "Jira"]
    
    criteria = {
        "cost": 0.25,
        "features": 0.25,
        "usability": 0.20,
        "integration": 0.15,
        "support": 0.15
    }
    
    ranker = get_comparison_ranker()
    result = ranker.compare_items(items, criteria)
    
    return ToolResult.ok(
        f"Ranked {len(items)} project management tools",
        {
            "ranking": result.ranking,
            "recommended": result.recommended,
            "scores": result.scores,
            "criteria_weights": result.criteria
        }
    )


# ============================================================================
# Decision Support Tools
# ============================================================================

async def analyze_business_options(options: list[dict]) -> ToolResult:
    """Analyze business options and provide recommendation.
    
    Args:
        options: List of options with name, description, factors, risks
        
    Returns:
        ToolResult with decision recommendation
    """
    engine = get_decision_engine()
    
    result = engine.analyze_options(options)
    
    return ToolResult.ok(
        f"Analyzed {len(options)} business options",
        {
            "recommended_option": result.recommended_option,
            "confidence": result.confidence,
            "rationale": result.rationale,
            "alternatives": result.alternatives,
            "key_factors": result.key_factors
        }
    )


async def calculate_investment_roi(investment: float, 
                                    returns: dict[str, float],
                                    months: int = 12) -> ToolResult:
    """Calculate ROI for an investment.
    
    Args:
        investment: Initial investment amount
        returns: Dictionary of return amounts by category
        months: Investment timeframe in months
        
    Returns:
        ToolResult with ROI metrics
    """
    engine = get_decision_engine()
    
    roi_data = engine.calculate_roi(investment, returns, months)
    
    return ToolResult.ok(
        "ROI calculation completed",
        roi_data
    )


async def evaluate_startup_idea(market_size: str | float,
                               competition: str | int,
                               technical_feasibility: int,
                               team_experience: int) -> ToolResult:
    """Evaluate a startup idea using multiple factors.
    
    Args:
        market_size: Estimated market size ($ or number)
        competition: Competition level (1-5, 5 = high)
        technical_feasibility: Feasibility score (1-5)
        team_experience: Experience score (1-5)
        
    Returns:
        ToolResult with evaluation
    """
    engine = get_decision_engine()
    
    # Normalize market size
    if isinstance(market_size, str):
        market_size = float(market_size.replace("$", "").replace(",", ""))
    
    # Calculate composite score
    market_score = min(1.0, market_size / 10000000) if market_size > 0 else 0.5  # Normalize to $10M max
    competition_score = 1.0 - (int(competition) / 5)  # Invert: lower competition = higher score
    
    composite_score = (
        market_score * 0.35 +
        competition_score * 0.20 +
        (int(technical_feasibility) / 5) * 0.25 +
        (int(team_experience) / 5) * 0.20
    )
    
    # Generate recommendation
    if composite_score >= 0.7:
        recommendation = "strong recommend proceeding"
    elif composite_score >= 0.5:
        recommendation = "recommend proceeding with caution"
    else:
        recommendation = "recommend further validation before proceeding"
    
    return ToolResult.ok(
        f"Startup evaluation complete: {recommendation}",
        {
            "composite_score": round(composite_score, 3),
            "breakdown": {
                "market_opportunity": round(market_score, 3),
                "competition": round(competition_score, 3),
                "technical_feasibility": round(int(technical_feasibility) / 5, 3),
                "team_experience": round(int(team_experience) / 5, 3)
            },
            "recommendation": recommendation,
            "raw_inputs": {
                "market_size": market_size,
                "competition": competition,
                "technical_feasibility": technical_feasibility,
                "team_experience": team_experience
            }
        }
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(verify_claim, permission=PermissionLevel.AUTO,
                  category="verification", tags=["fact-checking", "research"])
registry.register(check_source_credibility, permission=PermissionLevel.AUTO,
                  category="verification", tags=["credibility", "sourcing"])
registry.register(compare_items, permission=PermissionLevel.AUTO,
                  category="comparison", tags=["ranking", "comparison"])
registry.register(rank_project_management_tools, permission=PermissionLevel.AUTO,
                  category="comparison", tags=["tools", "ranking"])
registry.register(analyze_business_options, permission=PermissionLevel.AUTO,
                  category="decision", tags=["analysis", "options"])
registry.register(calculate_investment_roi, permission=PermissionLevel.AUTO,
                  category="decision", tags=["finance", "roi"])
registry.register(evaluate_startup_idea, permission=PermissionLevel.AUTO,
                  category="decision", tags=["business", "evaluation"])