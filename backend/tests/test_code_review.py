"""Tests for Code Review Enhancement (Phase 6).

Run with: python -m pytest backend/tests/test_code_review.py -v
"""
import pytest

from app.services.code_review import (
    CodeReviewer,
    SecurityIssue,
    PerformanceIssue,
    SuggestedChange,
    ComplexityReport,
    get_reviewer,
)


class TestCodeReviewer:
    """Test the CodeReviewer service."""
    
    def test_reviewer_singleton(self):
        """Test that get_reviewer returns singleton."""
        r1 = get_reviewer()
        r2 = get_reviewer()
        assert r1 is r2
    
    def test_reviewer_creation(self):
        """Test CodeReviewer can be created."""
        reviewer = CodeReviewer()
        assert reviewer is not None


class TestSecurityCheck:
    """Test security vulnerability detection."""
    
    def test_check_security_no_issues(self):
        """Test safe code has no security issues."""
        reviewer = get_reviewer()
        code = """
def safe_function(data):
    return data.strip()
        """
        issues = reviewer.check_security(code)
        assert len(issues) == 0
    
    def test_detect_hardcoded_secret(self):
        """Test detection of hardcoded secrets."""
        reviewer = get_reviewer()
        code = 'password = "mysecret123"'
        issues = reviewer.check_security(code)
        
        assert len(issues) > 0
        assert any("hardcoded" in i.category.lower() or "secret" in i.message.lower() for i in issues)
    
    def test_detect_eval_usage(self):
        """Test detection of eval usage."""
        reviewer = get_reviewer()
        code = 'result = eval(user_input)'
        issues = reviewer.check_security(code)
        
        assert any("eval" in i.message.lower() for i in issues)
    
    def test_detect_sql_injection(self):
        """Test detection of SQL injection patterns."""
        reviewer = get_reviewer()
        code = "cursor.execute(f'SELECT * FROM users WHERE id = {user_id}')"
        issues = reviewer.check_security(code)
        
        assert any("injection" in i.category.lower() or "sql" in i.message.lower() for i in issues)
    
    def test_security_issue_structure(self):
        """Test SecurityIssue dataclass structure."""
        issue = SecurityIssue(
            severity="high",
            message="Test issue",
            line=1,
            column=0,
            suggestion="Fix it",
            category="test"
        )
        
        assert issue.severity == "high"
        assert issue.message == "Test issue"


class TestPerformanceCheck:
    """Test performance issue detection."""
    
    def test_check_performance_no_issues(self):
        """Test clean code has no performance issues."""
        reviewer = get_reviewer()
        code = """
def process(items):
    return [item for item in items if item is not None]
        """
        issues = reviewer.check_performance(code)
        # List comprehensions are fine
    
    def test_detect_mutable_default(self):
        """Test detection of mutable default arguments."""
        reviewer = get_reviewer()
        code = "def func(items=[]): pass"
        issues = reviewer.check_performance(code)
        
        assert any("mutable" in i.message.lower() for i in issues)
    
    def test_performance_issue_structure(self):
        """Test PerformanceIssue dataclass structure."""
        issue = PerformanceIssue(
            severity="warning",
            message="Test issue",
            line=1,
            suggestion="Fix it"
        )
        
        assert issue.severity == "warning"


class TestComplexityAnalysis:
    """Test code complexity analysis."""
    
    def test_simple_code_low_complexity(self):
        """Test simple code has low complexity."""
        reviewer = get_reviewer()
        code = """
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b
        """
        report = reviewer.complexity_analysis(code)
        
        assert report.total_lines > 0
        assert report.functions == 2
        assert report.complexity_grade in ["A", "B", "C", "D", "F"]
    
    def test_complex_code_high_complexity(self):
        """Test complex code has higher complexity grade."""
        reviewer = get_reviewer()
        code = """
def complex_func(a):
    if a > 0:
        if a > 10:
            if a > 20:
                return "very high"
            return "high"
        return "medium"
    return "low"
        """
        report = reviewer.complexity_analysis(code)
        
        assert report.functions == 1
        assert report.cyclomatic_complexity > 1
    
    def test_complexity_report_structure(self):
        """Test ComplexityReport dataclass structure."""
        report = ComplexityReport(
            total_lines=100,
            cyclomatic_complexity=5,
            functions=3,
            classes=1,
            avg_function_length=33.3,
            complexity_grade="B"
        )
        
        assert report.total_lines == 100
        assert report.functions == 3
        assert report.complexity_grade == "B"
    
    def test_syntax_error_handling(self):
        """Test complexity analysis handles syntax errors."""
        reviewer = get_reviewer()
        code = "def unclosed("  # Invalid syntax
        report = reviewer.complexity_analysis(code)
        
        # Should still return a valid report
        assert report.complexity_grade == "F"


class TestSuggestedChanges:
    """Test suggested code changes."""
    
    def test_suggested_change_structure(self):
        """Test SuggestedChange dataclass structure."""
        change = SuggestedChange(
            line=10,
            old_code="s += x",
            new_code="''.join(items)",
            reason="More efficient"
        )
        
        assert change.line == 10
        assert change.old_code == "s += x"


class TestIntegration:
    """Integration tests for code review tools."""
    
    def test_security_suggestion_method(self):
        """Test _get_security_suggestion returns appropriate messages."""
        reviewer = get_reviewer()
        
        # Test various issue types
        assert "environment" in reviewer._get_security_suggestion("hardcoded_secret").lower()
        assert "parameterized" in reviewer._get_security_suggestion("sql_injection").lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])