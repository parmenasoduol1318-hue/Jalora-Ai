"""Fact verification and claim checking tools (Phase 3).

Tools for verifying claims, checking facts, and validating information.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services.fact_verification import (
    get_fact_verifier,
    FactVerifier,
    VerificationMethod,
    CredibilityLevel,
)
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Verification Tools
# ============================================================================

async def verify_claim(
    claim: str,
    max_sources: int = 3,
    include_evidence: bool = False
) -> ToolResult:
    """Verify a claim using available evidence sources.
    
    Args:
        claim: The claim to verify
        max_sources: Maximum number of sources to check (default: 3)
        include_evidence: Whether to include full evidence in response
        
    Returns:
        ToolResult with verification result and confidence score
    """
    verifier = get_fact_verifier()
    result = verifier.verify_claim(claim, max_sources)
    
    response = {
        "claim": result.claim,
        "verified": result.verified,
        "confidence": round(result.confidence, 3),
        "method": result.method.value,
        "summary": result.summary,
        "evidence_count": len(result.evidence),
    }
    
    if include_evidence:
        response["evidence"] = [
            {
                "title": e.title,
                "url": e.url,
                "credibility": e.credibility.value,
                "summary": e.summary,
            }
            for e in result.evidence[:5]
        ]
    
    return ToolResult.ok(
        f"Claim verification: {'Verified' if result.verified else 'Unverified'} "
        f"(confidence: {result.confidence:.0%})",
        response
    )


async def check_fact(
    statement: str,
    context: str | None = None
) -> ToolResult:
    """Quick fact check for a statement.
    
    Args:
        statement: The factual statement to check
        context: Optional context or topic area
        
    Returns:
        ToolResult with quick verification
    """
    verifier = get_fact_verifier()
    result = verifier.verify_claim(statement)
    
    status = "✅ Verified" if result.verified else "❌ Unverified"
    confidence_str = "high" if result.confidence > 0.8 else "medium" if result.confidence > 0.5 else "low"
    
    return ToolResult.ok(
        f"{status} (confidence: {confidence_str})",
        {
            "statement": result.claim,
            "verified": result.verified,
            "confidence_level": confidence_str,
            "confidence": result.confidence,
            "method": result.method.value,
            "summary": result.summary,
        }
    )


async def compare_sources(sources: list[dict]) -> ToolResult:
    """Compare multiple sources for credibility.
    
    Args:
        sources: List of source dictionaries with 'url' and optionally 'title'
        
    Returns:
        ToolResult with source credibility assessment
    """
    if not sources:
        return ToolResult.fail("No sources provided", "Please provide at least one source")
    
    assessments = []
    total_reliability = 0
    
    for source in sources:
        url = source.get("url", "")
        title = source.get("title", "Unknown source")
        
        # Basic credibility heuristics (would be enhanced with actual analysis)
        credibility = CredibilityLevel.MEDIUM
        confidence = 0.5
        
        if ".gov" in url or ".edu" in url:
            credibility = CredibilityLevel.HIGH
            confidence = 0.8
        elif ".org" in url:
            credibility = CredibilityLevel.MEDIUM
            confidence = 0.6
        elif any(expert in url for expert in ["wikipedia", "encyclopedia"]):
            credibility = CredibilityLevel.MEDIUM
            confidence = 0.7
        
        assessments.append({
            "url": url,
            "title": title,
            "credibility": credibility.value,
            "confidence": confidence,
        })
        total_reliability += confidence
    
    avg_reliability = total_reliability / len(sources)
    overall_credibility = (
        "high" if avg_reliability > 0.7 
        else "medium" if avg_reliability > 0.4 
        else "low"
    )
    
    return ToolResult.ok(
        f"Analyzed {len(sources)} sources (avg reliability: {overall_credibility})",
        {
            "sources": assessments,
            "average_reliability": round(avg_reliability, 2),
            "overall_credibility": overall_credibility,
        }
    )


async def estimate_likelihood(
    event_description: str,
    factors: list[str] | None = None
) -> ToolResult:
    """Estimate likelihood of an event based on described factors.
    
    Args:
        event_description: Description of the event
        factors: List of relevant factors
        
    Returns:
        ToolResult with likelihood estimate
    """
    # Simplified likelihood estimation
    base_likelihood = 0.5  # Default 50%
    
    if factors:
        # Adjust based on positive factors
        positive_factors = sum(1 for f in factors if "likely" in f.lower() or "high" in f.lower())
        negative_factors = sum(1 for f in factors if "unlikely" in f.lower() or "low" in f.lower())
        
        adjustment = (positive_factors - negative_factors) * 0.1
        base_likelihood = max(0.0, min(1.0, base_likelihood + adjustment))
    
    likelihood_str = (
        "Very likely" if base_likelihood > 0.8
        else "Likely" if base_likelihood > 0.6
        else "Possible" if base_likelihood > 0.4
        else "Unlikely" if base_likelihood > 0.2
        else "Very unlikely"
    )
    
    return ToolResult.ok(
        f"Estimated likelihood: {likelihood_str} ({base_likelihood:.0%})",
        {
            "event": event_description,
            "estimated_probability": round(base_likelihood, 3),
            "likelihood_label": likelihood_str,
            "factors_considered": factors or [],
        }
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(
    verify_claim,
    permission=PermissionLevel.AUTO,
    category="verification",
    tags=["verify", "claim", "fact", "check"]
)

registry.register(
    check_fact,
    permission=PermissionLevel.AUTO,
    category="verification",
    tags=["fact", "check", "verify"]
)

registry.register(
    compare_sources,
    permission=PermissionLevel.AUTO,
    category="verification",
    tags=["source", "credibility", "compare"]
)

registry.register(
    estimate_likelihood,
    permission=PermissionLevel.AUTO,
    category="verification",
    tags=["likelihood", "probability", "estimate"]
)