"""Code review enhancement services (Phase 6).

Provides security, performance, and correctness analysis for code.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# Code Review Models
# ============================================================================

@dataclass
class SecurityIssue:
    """Represents a security issue found in code."""
    severity: str  # critical, high, medium, low
    message: str
    line: int | None
    column: int | None
    suggestion: str
    category: str  # injection, auth, secret, etc.


@dataclass
class PerformanceIssue:
    """Represents a performance issue found in code."""
    severity: str  # warning, info
    message: str
    line: int | None
    suggestion: str
    complexity: str = "low"  # low, medium, high


@dataclass  
class SuggestedChange:
    """Represents a suggested code change."""
    line: int | None
    old_code: str
    new_code: str
    reason: str


@dataclass
class ComplexityReport:
    """Report on code complexity metrics."""
    total_lines: int
    cyclomatic_complexity: int
    functions: int
    classes: int
    avg_function_length: float
    complexity_grade: str  # A, B, C, D, F


# ============================================================================
# Code Reviewer Service
# ============================================================================

class CodeReviewer:
    """Service for analyzing code for security, performance, and correctness."""
    
    def __init__(self):
        self.security_patterns = self._get_security_patterns()
        self.performance_patterns = self._get_performance_patterns()
    
    def _get_security_patterns(self) -> dict[str, re.Pattern]:
        """Get regex patterns for security issues."""
        return {
            "hardcoded_secret": re.compile(r'(?i)(password|secret|key|token)\s*[:=]\s*["\'][^"\']{4,}["\']'),
            "sql_injection": re.compile(r'execute\s*\([^)]*format\(|execute\s*\([^)]*\+'),
            "eval_usage": re.compile(r'\beval\s*\('),
            "exec_usage": re.compile(r'\bexec\s*\('),
            "shell_true": re.compile(r'shell=True'),
            "pickle_load": re.compile(r'pickle\.loads?\s*\('),
            "yaml_load": re.compile(r'yaml\.load\s*\('),
            "subprocess_shell": re.compile(r'subprocess\.\w+\([^)]*shell\s*=\s*True'),
            "f_string_sql": re.compile(r'f["\'].*SELECT.*\{'),
        }
    
    def _get_performance_patterns(self) -> dict[str, re.Pattern]:
        """Get regex patterns for performance issues."""
        return {
            "mutable_default": re.compile(r'def\s+\w+\s*\([^)]*=\s*\[|\{\}'),
            "catch_all_except": re.compile(r'except\s*:\s*$'),
            "bare_raise": re.compile(r'raise\s*,\s*$'),
            "long_function": re.compile(r'^def\s+\w+\s*\([^)]*\):\s*$'),  # heuristic
        }
    
    def check_security(self, code: str, language: str = "python") -> list[SecurityIssue]:
        """Check code for security vulnerabilities."""
        issues = []
        lines = code.split('\n')
        
        for pattern_name, pattern in self.security_patterns.items():
            for match in pattern.finditer(code):
                # Find line number
                line_num = code[:match.start()].count('\n') + 1
                line_start = match.start()
                for i, line in enumerate(lines):
                    if i == line_num - 1:
                        col = match.start() - (len(code) - len('\n'.join(lines[:line_num])))
                        break
                else:
                    col = 0
                
                severity = "critical" if pattern_name in ("eval_usage", "exec_usage", "sql_injection") else "high"
                
                issues.append(SecurityIssue(
                    severity=severity,
                    message=f"Potential security issue: {pattern_name}",
                    line=line_num,
                    column=col,
                    suggestion=self._get_security_suggestion(pattern_name),
                    category=pattern_name.replace("_", " ")
                ))
        
        return issues
    
    def _get_security_suggestion(self, issue_type: str) -> str:
        """Get suggestion for a security issue type."""
        suggestions = {
            "hardcoded_secret": "Use environment variables or a secrets manager",
            "sql_injection": "Use parameterized queries or ORM",
            "eval_usage": "Avoid eval, use ast.literal_eval or a safe parser",
            "exec_usage": "Avoid exec, use importlib or other safe methods",
            "shell_true": "Avoid shell=True, pass arguments as a list",
            "pickle_load": "Use json or yaml.safe_load instead",
            "yaml_load": "Use yaml.safe_load instead of yaml.load",
            "subprocess_shell": "Use subprocess with shell=False",
            "f_string_sql": "Use parameterized queries, not f-strings for SQL",
        }
        return suggestions.get(issue_type, "Review and fix this issue")
    
    def check_performance(self, code: str, language: str = "python") -> list[PerformanceIssue]:
        """Check code for performance issues."""
        issues = []
        lines = code.split('\n')
        
        # Check for mutable defaults
        for match in self.performance_patterns["mutable_default"].finditer(code):
            line_num = code[:match.start()].count('\n') + 1
            issues.append(PerformanceIssue(
                severity="warning",
                message="Mutable default argument may cause unexpected behavior",
                line=line_num,
                suggestion="Use None and create the list/dict in the function body",
                complexity="low"
            ))
        
        # Check for bare except
        for match in self.performance_patterns["catch_all_except"].finditer(code):
            line_num = code[:match.start()].count('\n') + 1
            issues.append(PerformanceIssue(
                severity="warning",
                message="Bare except catches all exceptions including KeyboardInterrupt",
                line=line_num,
                suggestion="Catch specific exceptions instead",
                complexity="low"
            ))
        
        return issues
    
    def suggest_improvements(self, code: str) -> list[SuggestedChange]:
        """Suggest code improvements."""
        suggestions = []
        
        # Check for common patterns to improve
        # String concatenation in loops
        if re.search(r'\.join\(', code):
            pass  # Good pattern
        elif re.search(r'for\s+\w+\s+in\s+\w+:\s*\w+\s*\+=', code):
            suggestions.append(SuggestedChange(
                line=None,
                old_code="s = ''\nfor x in items:\n    s += x",
                new_code="s = ''.join(items)",
                reason="Use join() instead of string concatenation in loops"
            ))
        
        return suggestions
    
    def complexity_analysis(self, code: str) -> ComplexityReport:
        """Analyze code complexity."""
        lines = [l for l in code.split('\n') if l.strip()]
        
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return ComplexityReport(
                total_lines=len(lines),
                cyclomatic_complexity=0,
                functions=0,
                classes=0,
                avg_function_length=0.0,
                complexity_grade="F"
            )
        
        functions = 0
        classes = 0
        total_complexity = 1  # Base complexity
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions += 1
                # Count branches in function
                for child in ast.walk(node):
                    if isinstance(child, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
                        total_complexity += 1
            elif isinstance(node, ast.ClassDef):
                classes += 1
        
        # Calculate grade
        if total_complexity <= 5:
            grade = "A"
        elif total_complexity <= 10:
            grade = "B"
        elif total_complexity <= 15:
            grade = "C"
        elif total_complexity <= 20:
            grade = "D"
        else:
            grade = "F"
        
        avg_func_length = len(lines) / max(functions, 1)
        
        return ComplexityReport(
            total_lines=len(lines),
            cyclomatic_complexity=total_complexity,
            functions=functions,
            classes=classes,
            avg_function_length=avg_func_length,
            complexity_grade=grade
        )


# Singleton instance
_reviewer: CodeReviewer | None = None

def get_reviewer() -> CodeReviewer:
    """Get or create the code reviewer singleton."""
    global _reviewer
    if _reviewer is None:
        _reviewer = CodeReviewer()
    return _reviewer