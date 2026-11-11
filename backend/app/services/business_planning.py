"""Business planning tools and templates (spec §7).

Provides structured business plan creation, market analysis, financial 
projections, risk assessment, and milestone tracking.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.business import (
    BusinessPlan, 
    MarketAnalysis, 
    FinancialProjection,
    RiskAssessment,
    Milestone
)
from app.models.user import User

logger = logging.getLogger("jalora.business")


# ============================================================================
# Templates
# ============================================================================

BUSINESS_TEMPLATES = {
    "market_analysis": {
        "sections": [
            "executive_summary",
            "market_overview",
            "target_segments", 
            "competitor_analysis",
            "market_opportunities",
            "key_findings"
        ],
        "questions": [
            "What is the total addressable market (TAM) size?",
            "What is the growing rate of this market?",
            "Who are the primary customer segments?",
            "Who are the top 5 competitors?",
            "What are the key opportunities in this market?"
        ]
    },
    "financial_projections": {
        "sections": ["revenue_projection", "cost_structure", "profit_analysis", "break_even"],
        "questions": [
            "What are the projected monthly revenues for the next 12 months?",
            "What are the fixed and variable costs?",
            "What is the expected profit margin?",
            "When will the business break even?"
        ]
    },
    "product_launch": {
        "sections": ["product_definition", "target_market", "go_to_market", "success_metrics"],
        "questions": [
            "What is the product being launched?",
            "Who is the target market?",
            "What is the launch strategy?",
            "How will success be measured?"
        ]
    },
    "risk_assessment": {
        "risk_categories": ["market", "operational", "financial", "legal", "technical"],
        "questions": [
            "What are the key market risks?",
            "What operational risks could impact success?",
            "What financial risks need mitigation?",
            "What legal or compliance issues exist?",
            "What technical risks are present?"
        ]
    }
}


# ============================================================================
# Business Plan Generator
# ============================================================================

@dataclass
class PlanSection:
    """A section of a business plan."""
    title: str
    content: str
    key_points: list[str]
    questions: list[str] | None = None


@dataclass
class MarketInsight:
    """Insight from market analysis."""
    market_size: float
    growth_rate: float
    segments: list[dict]
    competitors: list[dict]
    opportunities: list[str]


class BusinessPlanningAssistant:
    """Assists with business planning tasks."""
    
    def __init__(self):
        self.templates = BUSINESS_TEMPLATES
    
    def create_plan(self, user_id: str, name: str, plan_type: str) -> dict:
        """Create a new business plan."""
        db = SessionLocal()
        try:
            plan = BusinessPlan(
                user_id=user_id,
                name=name,
                type=plan_type,
                template=self._get_template(plan_type)
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)
            return {
                "id": plan.id,
                "name": plan.name,
                "type": plan.type,
                "status": plan.status
            }
        finally:
            db.close()
    
    def _get_template(self, plan_type: str) -> str:
        """Get template name for plan type."""
        return plan_type if plan_type in self.templates else "standard"
    
    def analyze_market(self, user_input: dict, plan_id: str | None = None) -> dict:
        """Analyze market data and extract insights."""
        # Extract market size
        market_size = self._extract_number(user_input.get("market_size", 0))
        growth_rate = self._extract_number(user_input.get("growth_rate", 0))
        
        # Parse segments
        segments = user_input.get("segments", [])
        if isinstance(segments, str):
            segments = [{"name": s.strip()} for s in segments.split(",")]
        
        # Parse competitors
        competitors = user_input.get("competitors", [])
        if isinstance(competitors, str):
            competitors = [{"name": c.strip()} for c in competitors.split(",")]
        
        # Identify opportunities
        opportunities = user_input.get("opportunities", [])
        if isinstance(opportunities, str):
            opportunities = [o.strip() for o in opportunities.split(";")]
        
        insight = MarketInsight(
            market_size=market_size,
            growth_rate=growth_rate,
            segments=segments,
            competitors=competitors,
            opportunities=opportunities
        )
        
        return {
            "market_size": insight.market_size,
            "growth_rate": insight.growth_rate,
            "segments": insight.segments,
            "competitors": insight.competitors,
            "opportunities": insight.opportunities,
            "summary": self._generate_market_summary(insight)
        }
    
    def _extract_number(self, value: Any) -> float:
        """Extract numeric value from various formats."""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # Remove currency symbols, commas, etc.
            cleaned = re.sub(r'[^\d.]', '', value.replace(",", ""))
            try:
                return float(cleaned) if cleaned else 0.0
            except ValueError:
                return 0.0
        return 0.0
    
    def _generate_market_summary(self, insight: MarketInsight) -> str:
        """Generate a market summary from insights."""
        size_str = f"${insight.market_size:,.0f}" if insight.market_size > 0 else "not specified"
        growth_str = f"{insight.growth_rate:.1f}%" if insight.growth_rate else "not specified"
        
        summary = f"Market Size: {size_str}. Growth Rate: {growth_str}. "
        summary += f"Segments: {len(insight.segments)}. "
        summary += f"Competitors: {len(insight.competitors)}. "
        summary += f"Opportunities identified: {len(insight.opportunities)}."
        return summary
    
    def create_financial_projection(self, plan_id: str, year: int, 
                                   quarterly: bool = True) -> dict:
        """Create financial projection structure."""
        db = SessionLocal()
        projections = []
        
        try:
            for q in range(1, 5):
                proj = FinancialProjection(
                    plan_id=plan_id,
                    period="quarterly" if quarterly else "yearly",
                    year=year,
                    quarter=q if quarterly else None,
                    revenue=0.0,
                    expenses=0.0,
                    profit=0.0
                )
                db.add(proj)
                projections.append({
                    "id": proj.id,
                    "period": proj.period,
                    "year": proj.year,
                    "quarter": proj.quarter
                })
            db.commit()
        finally:
            db.close()
        
        return {"projections": projections}
    
    def assess_risks(self, risk_data: list[dict], plan_id: str | None = None) -> list[dict]:
        """Assess and rank risks."""
        assessed = []
        
        for risk in risk_data:
            probability = self._extract_number(risk.get("probability", 0))
            impact = self._extract_number(risk.get("impact", 0))
            risk_score = probability * impact
            
            assessed.append({
                "risk_name": risk.get("risk_name", "Unknown"),
                "category": risk.get("category", "general"),
                "probability": probability,
                "impact": impact,
                "risk_score": round(risk_score, 3),
                "priority": "high" if risk_score > 0.5 else "medium" if risk_score > 0.2 else "low"
            })
        
        # Sort by risk score descending
        return sorted(assessed, key=lambda x: x["risk_score"], reverse=True)
    
    def create_milestone_plan(self, milestones: list[dict], plan_id: str) -> list[dict]:
        """Create milestone plan from input."""
        result = []
        
        for m in milestones:
            db = SessionLocal()
            try:
                milestone = Milestone(
                    plan_id=plan_id,
                    name=m.get("name", "Untitled"),
                    description=m.get("description", ""),
                    planned_date=m.get("planned_date"),
                    dependencies=m.get("dependencies", []),
                    status="pending"
                )
                db.add(milestone)
                db.commit()
                db.refresh(milestone)
                
                result.append({
                    "id": milestone.id,
                    "name": milestone.name,
                    "planned_date": milestone.planned_date.isoformat() if milestone.planned_date else None,
                    "status": milestone.status,
                    "completed": milestone.completed
                })
            finally:
                db.close()
        
        return result
    
    def generate_recommendation(self, plan_type: str, data: dict) -> dict:
        """Generate next-step recommendations based on plan type and data."""
        recommendations = []
        
        if plan_type == "market_analysis":
            if data.get("market_size", 0) > 0:
                recommendations.append("Validate market demand with customer interviews")
            if data.get("competitors"):
                recommendations.append("Create competitive positioning strategy")
            recommendations.append("Define target customer personas")
        
        elif plan_type == "financial_projections":
            recommendations.append("Build financial model with best/worst case scenarios")
            recommendations.append("Identify key value drivers")
            recommendations.append("Plan funding requirements")
        
        elif plan_type == "risk_assessment":
            recommendations.append("Create risk mitigation plans for top risks")
            recommendations.append("Assign risk owners")
            recommendations.append("Set up monitoring triggers")
        
        return {
            "plan_type": plan_type,
            "recommendations": recommendations
        }


# Singleton instance
_business_assistant: BusinessPlanningAssistant | None = None

def get_business_assistant() -> BusinessPlanningAssistant:
    """Get or create the business planning assistant singleton."""
    global _business_assistant
    if _business_assistant is None:
        _business_assistant = BusinessPlanningAssistant()
    return _business_assistant