"""Outcome evaluation and learning system (spec §1, §14).

Provides services for tracking, evaluating, and learning from task outcomes
to improve future performance and user experience.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.outcome import (
    TaskOutcome,
    OutcomeEvaluation,
    OutcomeMetric,
    SuccessRateTracker,
    LearningUpdate,
    OUTCOME_SUCCESS,
    QUALITY_HIGH,
    QUALITY_MEDIUM,
    QUALITY_LOW,
)
from app.models.actions import UserAction

logger = logging.getLogger("jalora.outcome_evaluation")


@dataclass
class OutcomeAnalysis:
    """Analysis of task outcomes for learning."""
    
    # Aggregate metrics
    total_outcomes: int = 0
    success_rate: float = 0.0
    average_quality: float = 0.0
    average_duration: float = 0.0
    
    # Trend analysis
    recent_success_rate: float = 0.0
    improvement_trend: float = 0.0  # Positive = improving
    
    # Tool-specific analysis
    tool_performance: dict[str, SuccessRateTracker] = field(default_factory=dict)
    
    # Recommendations
    recommendations: list[str] = field(default_factory=list)


class OutcomeEvaluator:
    """Evaluates task outcomes and generates learning signals."""
    
    def __init__(self):
        """Initialize the evaluator."""
        self._cache: dict[str, SuccessRateTracker] = {}
    
    def record_outcome(self, 
                       task_id: str,
                       user_id: str,
                       tool_name: str,
                       success: bool,
                       quality_score: float = 0.5,
                       duration_seconds: float = 0.0,
                       input_description: str = "",
                       output_summary: str = "",
                       user_rating: int | None = None,
                       error_message: str = "") -> TaskOutcome:
        """Record a new outcome.
        
        Args:
            task_id: Unique task identifier
            user_id: User who executed the task
            tool_name: Name of the tool used
            success: Whether the task succeeded
            quality_score: Quality assessment (0.0-1.0)
            duration_seconds: Time taken to complete
            input_description: Description of the input
            output_summary: Summary of the output
            user_rating: Optional user rating (1-5)
            error_message: Error message if failed
            
        Returns:
            The created TaskOutcome
        """
        outcome = TaskOutcome(
            task_id=task_id,
            user_id=user_id,
            tool_name=tool_name,
            success=success,
            quality_score=quality_score,
            duration_seconds=duration_seconds,
            input_description=input_description,
            output_summary=output_summary,
            user_rating=user_rating if user_rating else (5 if success else 1),
            error_message=error_message,
        )
        
        logger.info(f"Recorded outcome for {tool_name}: success={success}, quality={quality_score:.2f}")
        return outcome
    
    def evaluate_outcome(self, outcome: TaskOutcome) -> OutcomeEvaluation:
        """Evaluate an outcome and generate insights.
        
        Args:
            outcome: The outcome to evaluate
            
        Returns:
            OutcomeEvaluation with metrics and recommendations
        """
        metrics = []
        
        # Quality metric
        quality_metric = OutcomeMetric(
            metric_name="quality",
            value=outcome.quality_score,
            threshold=0.5
        )
        metrics.append(quality_metric)
        
        # Success metric
        success_metric = OutcomeMetric(
            metric_name="success_rate",
            value=1.0 if outcome.success else 0.0
        )
        metrics.append(success_metric)
        
        # Duration efficiency (normalized)
        duration_normalized = min(1.0, outcome.duration_seconds / 60.0)  # Normalize to 1 minute
        duration_metric = OutcomeMetric(
            metric_name="duration_efficiency",
            value=1.0 - duration_normalized  # Lower duration = higher efficiency
        )
        metrics.append(duration_metric)
        
        # User satisfaction (if rating available)
        if outcome.user_rating:
            satisfaction_metric = OutcomeMetric(
                metric_name="satisfaction",
                value=outcome.user_rating / 5.0
            )
            metrics.append(satisfaction_metric)
        
        evaluation = OutcomeEvaluation(
            outcome=outcome,
            metrics=metrics
        )
        
        return evaluation
    
    def analyze_user_outcomes(self, user_id: str, 
                              days: int = 30) -> OutcomeAnalysis:
        """Analyze outcomes for a user over a time period.
        
        Args:
            user_id: User to analyze
            days: Number of days to analyze
            
        Returns:
            OutcomeAnalysis with insights
        """
        db = SessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Get recent outcomes (via action records)
            actions = db.query(UserAction).filter(
                UserAction.user_id == user_id,
                UserAction.started_at >= cutoff,
                UserAction.status == "completed"
            ).all()
            
            analysis = OutcomeAnalysis()
            analysis.total_outcomes = len(actions)
            
            if not actions:
                return analysis
            
            # Calculate metrics
            successful = sum(1 for a in actions if a.status == "completed")
            analysis.success_rate = successful / len(actions)
            analysis.recent_success_rate = analysis.success_rate
            
            # Tool performance tracking
            for action in actions:
                if action.tool_name not in analysis.tool_performance:
                    analysis.tool_performance[action.tool_name] = SuccessRateTracker(
                        tool_name=action.tool_name
                    )
                
                success = action.status == "completed"
                analysis.tool_performance[action.tool_name].update(success)
            
            # Generate recommendations
            self._generate_recommendations(analysis)
            
            return analysis
        finally:
            db.close()
    
    def _generate_recommendations(self, analysis: OutcomeAnalysis) -> None:
        """Generate recommendations based on analysis."""
        if analysis.success_rate < 0.7:
            analysis.recommendations.append(
                "Consider reviewing tool usage patterns - success rate is below target"
            )
        
        if analysis.success_rate > 0.9:
            analysis.recommendations.append(
                "Great success rate! Keep using current approach."
            )
        
        # Check for problematic tools
        for tool_name, tracker in analysis.tool_performance.items():
            if tracker.current_success_rate() < 0.5:
                analysis.recommendations.append(
                    f"Review {tool_name} - success rate is {tracker.current_success_rate():.0%}"
                )
    
    def generate_learning_updates(self, 
                                  outcome: TaskOutcome,
                                  user_id: str) -> list[LearningUpdate]:
        """Generate preference updates from an outcome.
        
        Args:
            outcome: The outcome to learn from
            user_id: User ID for preference updates
            
        Returns:
            List of LearningUpdate objects
        """
        updates = []
        
        # High quality outcome -> reinforce related preferences
        if outcome.quality_score >= 0.8:
            updates.append(LearningUpdate(
                user_id=user_id,
                preference_key="preferred_tool_tolerance",
                old_value=None,
                new_value=0.8,
                confidence_change=0.1,
                reason="High quality outcome achieved"
            ))
        
        # Failed outcome -> adjust tolerance
        if not outcome.success:
            updates.append(LearningUpdate(
                user_id=user_id,
                preference_key="error_tolerance",
                old_value=None,
                new_value=0.5,
                confidence_change=-0.1,
                reason="Outcome failed, adjusting tolerance"
            ))
        
        return updates
    
    def get_tool_performance(self, tool_name: str, 
                            user_id: str | None = None) -> SuccessRateTracker:
        """Get performance tracker for a specific tool.
        
        Args:
            tool_name: Name of the tool
            user_id: Optional user ID for user-specific tracking
            
        Returns:
            SuccessRateTracker for the tool
        """
        if tool_name not in self._cache:
            self._cache[tool_name] = SuccessRateTracker(tool_name=tool_name)
        
        return self._cache[tool_name]
    
    def clear_cache(self) -> None:
        """Clear the evaluation cache."""
        self._cache.clear()


# Singleton instance
_evaluator: OutcomeEvaluator | None = None


def get_outcome_evaluator() -> OutcomeEvaluator:
    """Get or create the outcome evaluator singleton."""
    global _evaluator
    if _evaluator is None:
        _evaluator = OutcomeEvaluator()
    return _evaluator


def analyze_feedback_outcome(user_id: str, 
                             feedback_data: dict) -> LearningUpdate:
    """Quick analysis from user feedback."""
    rating = feedback_data.get("rating", 3)
    
    return LearningUpdate(
        user_id=user_id,
        preference_key="feedback_response_preference",
        old_value=None,
        new_value=rating / 5.0,
        confidence_change=0.1 if rating >= 4 else -0.1,
        reason=f"User feedback rating: {rating}"
    )