"""Deployment preparation service (Phase 7).

Provides validation and preparation tools for deploying applications
and agent configurations to various environments.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.config import get_settings

logger = logging.getLogger("jalora.deployment_prep")


class DeploymentTarget(str, Enum):
    """Deployment target environments."""
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    CLOUD = "cloud"


class ValidationStatus(str, Enum):
    """Status of deployment validation."""
    VALID = "valid"
    WARNING = "warning"
    INVALID = "invalid"


@dataclass
class ValidationIssue:
    """Represents a validation issue."""
    severity: str  # error, warning, info
    message: str
    suggestion: str
    category: str  # config, security, performance, compatibility


@dataclass
class DeploymentChecklist:
    """Checklist for deployment readiness."""
    config_valid: bool = False
    secrets_configured: bool = False
    dependencies_resolved: bool = False
    tests_passed: bool = False
    performance_benchmarked: bool = False
    security_scanned: bool = False
    rollback_plan: bool = False
    monitoring_configured: bool = False


@dataclass
class DeploymentPlan:
    """Plan for a deployment."""
    target: DeploymentTarget
    version: str
    checklist: DeploymentChecklist
    timeline: list[tuple[str, datetime]] = field(default_factory=list)  # (step, timestamp)
    stakeholders: list[str] = field(default_factory=list)
    notes: str = ""
    
    def is_ready(self) -> bool:
        """Check if deployment is ready."""
        return all([
            self.checklist.config_valid,
            self.checklist.secrets_configured,
            self.checklist.tests_passed,
            self.checklist.security_scanned,
        ])


class DeploymentPreparer:
    """Prepares and validates deployments."""
    
    def __init__(self):
        """Initialize the deployment preparer."""
        self._validated_configs: dict[str, ValidationIssue] = {}
    
    def validate_deployment(
        self,
        target: DeploymentTarget,
        config: dict[str, Any] | None = None
    ) -> tuple[bool, list[ValidationIssue]]:
        """Validate a deployment configuration.
        
        Args:
            target: Deployment target
            config: Configuration to validate
            
        Returns:
            Tuple of (is_valid, list of issues)
        """
        issues = []
        
        if config is None:
            config = {}
        
        # Check secrets
        if not config.get("secrets_configured", False):
            if target == DeploymentTarget.PRODUCTION:
                issues.append(ValidationIssue(
                    severity="error",
                    message="Secrets not configured for production deployment",
                    suggestion="Configure JALORA_ env vars in deployment environment",
                    category="security",
                ))
            else:
                issues.append(ValidationIssue(
                    severity="warning",
                    message="Secrets configuration recommended",
                    suggestion="Consider configuring secrets for consistency",
                    category="security",
                ))
        
        # Check database URL
        db_url = config.get("database_url")
        if not db_url:
            issues.append(ValidationIssue(
                severity="error",
                message="Database URL not configured",
                suggestion="Set JALORA_DATABASE_URL",
                category="config",
            ))
        
        # Check JWT secret
        if target == DeploymentTarget.PRODUCTION:
            jwt_secret = config.get("jwt_secret", "")
            if not jwt_secret or jwt_secret == "development-secret":
                issues.append(ValidationIssue(
                    severity="error",
                    message="Invalid JWT secret for production",
                    suggestion="Set strong JALORA_JWT_SECRET",
                    category="security",
                ))
        
        # Check model configuration
        model_configured = config.get("model_provider") or config.get("default_provider")
        if not model_configured:
            issues.append(ValidationIssue(
                severity="warning",
                message="Model provider not explicitly configured",
                suggestion="Set JALORA_DEFAULT_PROVIDER",
                category="config",
            ))
        
        is_valid = not any(i.severity == "error" for i in issues)
        self._validated_configs[str(target)] = issues
        
        return is_valid, issues
    
    def prepare_deployment(
        self,
        target: DeploymentTarget,
        version: str,
        config: dict[str, Any] | None = None
    ) -> DeploymentPlan:
        """Prepare a deployment plan.
        
        Args:
            target: Deployment target
            version: Version to deploy
            config: Deployment configuration
            
        Returns:
            DeploymentPlan with readiness checklist
        """
        is_valid, issues = self.validate_deployment(target, config)
        
        # Build checklist
        checklist = DeploymentChecklist()
        
        checklist.config_valid = is_valid
        checklist.secrets_configured = bool(config and config.get("secrets_configured"))
        checklist.tests_passed = config and config.get("tests_passed", False)
        checklist.security_scanned = config and config.get("security_scanned", False)
        
        # Add timeline
        timeline = [
            ("validation", datetime.now(timezone.utc)),
        ]
        
        if checklist.tests_passed:
            timeline.append(("testing_complete", datetime.now(timezone.utc)))
        
        if target == DeploymentTarget.PRODUCTION:
            timeline.append(("production_approval", datetime.now(timezone.utc)))
        
        timeline.append(("deployment", datetime.now(timezone.utc)))
        
        stakeholder = "ops-team"
        if target == DeploymentTarget.PRODUCTION:
            stakeholder = "production-engineering"
        
        return DeploymentPlan(
            target=target,
            version=version,
            checklist=checklist,
            timeline=timeline,
            stakeholders=[stakeholder],
            notes="Deployment prepared from Jalora deployment service",
        )
    
    def get_deployment_template(self, target: DeploymentTarget) -> dict[str, Any]:
        """Get deployment template for a target environment."""
        templates = {
            DeploymentTarget.LOCAL: {
                "database_url": "sqlite:///data/jalora.db",
                "model_provider": "ollama",
                "ollama_model": "qwen2.5:7b",
                "debug": True,
            },
            DeploymentTarget.DEVELOPMENT: {
                "database_url": "postgresql://localhost/jalora_dev",
                "model_provider": "auto",
                "debug": True,
                "reload": True,
            },
            DeploymentTarget.STAGING: {
                "database_url": "postgresql://postgres@postgres/jalora_staging",
                "model_provider": "auto",
                "debug": False,
            },
            DeploymentTarget.PRODUCTION: {
                "database_url": "${JALORA_DATABASE_URL}",
                "model_provider": "auto",
                "debug": False,
                "workers": 4,
            },
        }
        
        return templates.get(target, templates[DeploymentTarget.STAGING])


# Singleton instance
_preparer: DeploymentPreparer | None = None


def get_deployment_preparer() -> DeploymentPreparer:
    """Get or create the deployment preparer singleton."""
    global _preparer
    if _preparer is None:
        _preparer = DeploymentPreparer()
    return _preparer


async def validate_deployment(
    target: str,
    config: dict | None = None
) -> ToolResult:
    """Validate a deployment configuration.
    
    Args:
        target: Deployment target (local, development, staging, production)
        config: Optional configuration dict
        
    Returns:
        ToolResult with validation result
    """
    try:
        deployment_target = DeploymentTarget(target.lower())
    except ValueError:
        return ToolResult.fail(
            "Invalid target",
            f"Unknown deployment target: {target}"
        )
    
    preparer = get_deployment_preparer()
    is_valid, issues = preparer.validate_deployment(deployment_target, config)
    
    status = ValidationStatus.VALID if is_valid else ValidationStatus.INVALID
    
    return ToolResult.ok(
        f"Deployment validation: {status.value}",
        {
            "target": target,
            "valid": is_valid,
            "issues": [
                {
                    "severity": i.severity,
                    "message": i.message,
                    "suggestion": i.suggestion,
                    "category": i.category,
                }
                for i in issues
            ],
        }
    )


async def generate_deployment_plan(
    target: str,
    version: str,
    with_template: bool = False
) -> ToolResult:
    """Generate a deployment plan.
    
    Args:
        target: Deployment target
        version: Version to deploy
        with_template: Include config template
        
    Returns:
        ToolResult with deployment plan
    """
    try:
        deployment_target = DeploymentTarget(target.lower())
    except ValueError:
        return ToolResult.fail(
            "Invalid target",
            f"Unknown deployment target: {target}"
        )
    
    preparer = get_deployment_preparer()
    plan = preparer.prepare_deployment(deployment_target, version)
    
    result = {
        "target": plan.target.value,
        "version": plan.version,
        "ready": plan.is_ready(),
        "checklist": {
            "config_valid": plan.checklist.config_valid,
            "secrets_configured": plan.checklist.secrets_configured,
            "tests_passed": plan.checklist.tests_passed,
            "security_scanned": plan.checklist.security_scanned,
        },
        "stakeholders": plan.stakeholders,
    }
    
    if with_template:
        result["template"] = preparer.get_deployment_template(deployment_target)
    
    return ToolResult.ok(
        f"Deployment plan generated for {target} v{version}",
        result
    )