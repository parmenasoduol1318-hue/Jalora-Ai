"""Business planning tools (spec §7).

Tools for creating business plans, market analysis, financial projections,
risk assessment, and milestone planning.
"""
from __future__ import annotations

from datetime import datetime

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry
from app.services.business_planning import get_business_assistant


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Business Plan Tools
# ============================================================================

async def create_business_plan(name: str, plan_type: str) -> ToolResult:
    """Create a new business plan.
    
    Args:
        name: Name of the business plan
        plan_type: Type of plan (market_analysis, financial_projections, product_launch, risk_assessment)
        
    Returns:
        ToolResult with created plan details
    """
    assistant = get_business_assistant()
    
    plan = assistant.create_plan(_user_id(), name, plan_type)
    
    return ToolResult.ok(
        f"Created business plan: {name}",
        plan
    )


async def analyze_market(market_data: dict) -> ToolResult:
    """Analyze market data and extract insights.
    
    Args:
        market_data: Dictionary with market_size, growth_rate, segments, 
                    competitors, opportunities
        
    Returns:
        ToolResult with market analysis insights
    """
    assistant = get_business_assistant()
    
    insights = assistant.analyze_market(market_data)
    
    return ToolResult.ok(
        "Market analysis completed",
        insights
    )


async def create_financial_projections(plan_id: str, year: int, quarterly: bool = True) -> ToolResult:
    """Create financial projections for a plan.
    
    Args:
        plan_id: Business plan ID
        year: Fiscal year for projections
        quarterly: Whether to create quarterly (vs yearly) projections
        
    Returns:
        ToolResult with created projections
    """
    assistant = get_business_assistant()
    
    projections = assistant.create_financial_projection(plan_id, year, quarterly)
    
    return ToolResult.ok(
        f"Created {year} financial projections ({'quarterly' if quarterly else 'yearly'})",
        projections
    )


async def assess_risks(risk_data: list[dict]) -> ToolResult:
    """Assess and rank risks.
    
    Args:
        risk_data: List of risks with probability, impact, category
        
    Returns:
        ToolResult with ranked risks
    """
    assistant = get_business_assistant()
    
    risks = assistant.assess_risks(risk_data)
    
    return ToolResult.ok(
        f"Assessed {len(risks)} risks",
        {"risks": risks}
    )


async def create_milestones(milestones: list[dict], plan_id: str) -> ToolResult:
    """Create milestone plan.
    
    Args:
        milestones: List of milestones with name, description, planned_date
        plan_id: Business plan ID
        
    Returns:
        ToolResult with created milestones
    """
    assistant = get_business_assistant()
    
    created = assistant.create_milestone_plan(milestones, plan_id)
    
    return ToolResult.ok(
        f"Created {len(created)} milestones",
        {"milestones": created}
    )


async def generate_plan_recommendations(plan_type: str, data: dict) -> ToolResult:
    """Generate next-step recommendations for a plan type.
    
    Args:
        plan_type: Type of business plan
        data: Plan-specific data
        
    Returns:
        ToolResult with recommendations
    """
    assistant = get_business_assistant()
    
    recommendations = assistant.generate_recommendation(plan_type, data)
    
    return ToolResult.ok(
        f"Generated recommendations for {plan_type}",
        recommendations
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(create_business_plan, permission=PermissionLevel.CONFIRM,
                  category="business_planning", tags=["planning", "business"])
registry.register(analyze_market, permission=PermissionLevel.AUTO,
                  category="business_planning", tags=["analysis", "market"])
registry.register(create_financial_projections, permission=PermissionLevel.AUTO,
                  category="business_planning", tags=["finance", "projections"])
registry.register(assess_risks, permission=PermissionLevel.AUTO,
                  category="business_planning", tags=["risk", "analysis"])
registry.register(create_milestones, permission=PermissionLevel.AUTO,
                  category="business_planning", tags=["planning", "milestones"])
registry.register(generate_plan_recommendations, permission=PermissionLevel.AUTO,
                  category="business_planning", tags=["recommendations", "planning"])