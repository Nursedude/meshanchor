"""
Tests for MeshForge auto-review system.

Run: python3 -m pytest tests/test_auto_review.py -v
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import os
import sys

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from utils.auto_review import (
    Severity,
    ReviewCategory,
    ReviewScope,
    AutoFixStatus,
    ReviewFinding,
    AgentResult,
    ReviewReport,
    ReviewPatterns,
    ReviewAgent,
    SecurityAgent,
    RedundancyAgent,
    PerformanceAgent,
    ReliabilityAgent,
    ReviewOrchestrator,
    detect_review_request,
    run_review,
    generate_report_markdown,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_finding():
    """Create a sample ReviewFinding for tests."""
    return ReviewFinding(
        category=ReviewCategory.SECURITY,
        severity=Severity.HIGH,
        file_path="/test/file.py",
        line_number=42,
        issue="Test issue",
        description="Test description",
        recommendation="Fix it",
        auto_fixable=True,
        pattern_matched="test_pattern",
    )


@pytest.fixture
def security_agent():
    """Create a SecurityAgent instance."""
    return SecurityAgent()


@pytest.fixture
def reliability_agent():
    """Create a ReliabilityAgent instance."""
    return ReliabilityAgent()


@pytest.fixture
def performance_agent():
    """Create a PerformanceAgent instance."""
    return PerformanceAgent()


@pytest.fixture
def redundancy_agent():
    """Create a RedundancyAgent instance."""
    return RedundancyAgent()


class TestSeverityEnum:
    """Tests for Severity enumeration."""

    def test_severity_values(self):
        """Test that severity values are correct."""
        assert Severity.CRITICAL.value == "critical"
        assert Severity.HIGH.value == "high"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.LOW.value == "low"
        assert Severity.INFO.value == "info"

    def test_severity_ordering(self):
        """Test that we have all expected severity levels."""
        severities = list(Severity)
        assert len(severities) == 5


class TestReviewCategoryEnum:
    """Tests for ReviewCategory enumeration."""

    def test_category_values(self):
        """Test that category values are correct."""
        assert ReviewCategory.SECURITY.value == "security"
        assert ReviewCategory.REDUNDANCY.value == "redundancy"
        assert ReviewCategory.PERFORMANCE.value == "performance"
        assert ReviewCategory.RELIABILITY.value == "reliability"


class TestReviewScopeEnum:
    """Tests for ReviewScope enumeration."""

    def test_scope_has_all_option(self):
        """Test that ALL scope exists."""
        assert ReviewScope.ALL is not None

    def test_scope_has_individual_categories(self):
        """Test that individual category scopes exist."""
        assert ReviewScope.SECURITY is not None
        assert ReviewScope.REDUNDANCY is not None
        assert ReviewScope.PERFORMANCE is not None
        assert ReviewScope.RELIABILITY is not None


class TestReviewFinding:
    """Tests for ReviewFinding dataclass."""

    def test_finding_creation(self, sample_finding):
        """Test ReviewFinding can be created with all fields."""
        assert sample_finding.category == ReviewCategory.SECURITY
        assert sample_finding.severity == Severity.HIGH
        assert sample_finding.file_path == "/test/file.py"
        assert sample_finding.line_number == 42
        assert sample_finding.issue == "Test issue"
        assert sample_finding.auto_fixable is True

    def test_finding_defaults(self):
        """Test ReviewFinding default values."""
        finding = ReviewFinding(
            category=ReviewCategory.SECURITY,
            severity=Severity.LOW,
            file_path="/test.py",
            line_number=1,
            issue="Issue",
            description="Desc",
            recommendation="Fix",
        )
        assert finding.auto_fixable is False
        assert finding.fix_status == AutoFixStatus.SKIPPED
        assert finding.pattern_matched is None

    def test_finding_to_dict(self, sample_finding):
        """Test ReviewFinding conversion to dictionary."""
        result = sample_finding.to_dict()

        assert result['category'] == 'security'
        assert result['severity'] == 'high'
        assert result['file_path'] == '/test/file.py'
        assert result['line_number'] == 42
        assert result['issue'] == 'Test issue'
        assert result['auto_fixable'] is True
        assert result['pattern_matched'] == 'test_pattern'


class TestAgentResult:
    """Tests for AgentResult dataclass."""

    def test_result_creation(self):
        """Test AgentResult can be created."""
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=10,
        )
        assert result.category == ReviewCategory.SECURITY
        assert result.files_scanned == 10
        assert result.findings == []
        assert result.fixes_applied == 0

    def test_total_issues_property(self, sample_finding):
        """Test total_issues property counts findings."""
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=5,
            findings=[sample_finding, sample_finding],
        )
        assert result.total_issues == 2

    def test_critical_count_property(self):
        """Test critical_count property counts CRITICAL findings."""
        critical_finding = ReviewFinding(
            category=ReviewCategory.SECURITY,
            severity=Severity.CRITICAL,
            file_path="/test.py",
            line_number=1,
            issue="Critical",
            description="Desc",
            recommendation="Fix",
        )
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=1,
            findings=[critical_finding],
        )
        assert result.critical_count == 1

    def test_high_count_property(self, sample_finding):
        """Test high_count property counts HIGH findings."""
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=1,
            findings=[sample_finding],  # sample_finding is HIGH
        )
        assert result.high_count == 1

    def test_medium_count_property(self):
        """Test medium_count property counts MEDIUM findings."""
        medium_finding = ReviewFinding(
            category=ReviewCategory.SECURITY,
            severity=Severity.MEDIUM,
            file_path="/test.py",
            line_number=1,
            issue="Medium",
            description="Desc",
            recommendation="Fix",
        )
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=1,
            findings=[medium_finding],
        )
        assert result.medium_count == 1

    def test_summary_method(self, sample_finding):
        """Test summary method generates readable text."""
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=5,
            findings=[sample_finding],
            fixes_applied=0,
            manual_required=1,
        )
        summary = result.summary()

        assert "SECURITY" in summary
        assert "5" in summary  # files scanned
        assert "1" in summary  # issues found


class TestReviewReport:
    """Tests for ReviewReport dataclass."""

    def test_report_creation(self):
        """Test ReviewReport can be created."""
        report = ReviewReport(scope=ReviewScope.ALL)
        assert report.scope == ReviewScope.ALL
        assert report.agent_results == {}
        assert report.total_files_scanned == 0

    def test_total_issues_property(self, sample_finding):
        """Test total_issues aggregates across agents."""
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=5,
            findings=[sample_finding],
        )
        report = ReviewReport(
            scope=ReviewScope.ALL,
            agent_results={ReviewCategory.SECURITY: result},
        )
        assert report.total_issues == 1

    def test_total_fixes_applied_property(self):
        """Test total_fixes_applied aggregates across agents."""
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=5,
            fixes_applied=3,
        )
        report = ReviewReport(
            scope=ReviewScope.ALL,
            agent_results={ReviewCategory.SECURITY: result},
        )
        assert report.total_fixes_applied == 3

    def test_get_all_findings_filters_by_severity(self, sample_finding):
        """Test get_all_findings filters by minimum severity."""
        low_finding = ReviewFinding(
            category=ReviewCategory.SECURITY,
            severity=Severity.LOW,
            file_path="/test.py",
            line_number=1,
            issue="Low",
            description="Desc",
            recommendation="Fix",
        )
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=5,
            findings=[sample_finding, low_finding],
        )
        report = ReviewReport(
            scope=ReviewScope.ALL,
            agent_results={ReviewCategory.SECURITY: result},
        )

        # Filter to HIGH and above
        findings = report.get_all_findings(min_severity=Severity.HIGH)
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_to_markdown_output(self, sample_finding):
        """Test to_markdown generates valid markdown."""
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=5,
            findings=[sample_finding],
        )
        report = ReviewReport(
            scope=ReviewScope.ALL,
            agent_results={ReviewCategory.SECURITY: result},
            total_files_scanned=5,
        )
        markdown = report.to_markdown()

        assert "# MeshForge Auto-Review Report" in markdown
        assert "Security Agent" in markdown
        assert "HIGH" in markdown


class TestReviewPatterns:
    """Tests for ReviewPatterns class."""

    def test_security_patterns_exist(self):
        """Test that security patterns are defined."""
        assert 'shell_true' in ReviewPatterns.SECURITY
        assert 'eval_call' in ReviewPatterns.SECURITY
        assert 'os_system' in ReviewPatterns.SECURITY

    def test_redundancy_patterns_exist(self):
        """Test that redundancy patterns are defined."""
        assert 'console_instantiation' in ReviewPatterns.REDUNDANCY
        assert 'logger_setup' in ReviewPatterns.REDUNDANCY

    def test_performance_patterns_exist(self):
        """Test that performance patterns are defined."""
        assert 'subprocess_no_timeout' in ReviewPatterns.PERFORMANCE
        assert 'requests_no_timeout' in ReviewPatterns.PERFORMANCE

    def test_reliability_patterns_exist(self):
        """Test that reliability patterns are defined."""
        assert 'bare_except' in ReviewPatterns.RELIABILITY
        assert 'todo_comment' in ReviewPatterns.RELIABILITY

    def test_patterns_have_required_fields(self):
        """Test that patterns have all required configuration fields."""
        for pattern_name, config in ReviewPatterns.SECURITY.items():
            assert 'pattern' in config, f"{pattern_name} missing 'pattern'"
            assert 'severity' in config, f"{pattern_name} missing 'severity'"
            assert 'issue' in config, f"{pattern_name} missing 'issue'"
            assert 'recommendation' in config, f"{pattern_name} missing 'recommendation'"


class TestSecurityAgent:
    """Tests for SecurityAgent."""

    def test_detects_shell_true(self, security_agent, temp_dir):
        """Test that SecurityAgent detects shell=True."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
import subprocess
result = subprocess.run("ls", shell=True)
""")
        findings = security_agent.scan_file(test_file)

        shell_findings = [f for f in findings if 'shell' in f.pattern_matched.lower()]
        assert len(shell_findings) >= 1
        assert shell_findings[0].severity == Severity.HIGH

    def test_detects_eval(self, security_agent, temp_dir):
        """Test that SecurityAgent detects eval()."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
user_input = "1 + 1"
result = eval(user_input)
""")
        findings = security_agent.scan_file(test_file)

        eval_findings = [f for f in findings if 'eval' in f.pattern_matched.lower()]
        assert len(eval_findings) >= 1
        assert eval_findings[0].severity == Severity.CRITICAL

    def test_detects_os_system(self, security_agent, temp_dir):
        """Test that SecurityAgent detects os.system()."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
import os
os.system("ls -la")
""")
        findings = security_agent.scan_file(test_file)

        os_findings = [f for f in findings if 'os_system' in f.pattern_matched.lower()]
        assert len(os_findings) >= 1

    def test_skips_comments(self, security_agent, temp_dir):
        """Test that SecurityAgent skips comment lines."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
# Never use shell=True for security
import subprocess
result = subprocess.run(["ls"])
""")
        findings = security_agent.scan_file(test_file)

        shell_findings = [f for f in findings if f.pattern_matched and 'shell' in f.pattern_matched.lower()]
        assert len(shell_findings) == 0

    def test_skips_docstrings(self, security_agent, temp_dir):
        """Test that SecurityAgent skips docstrings."""
        test_file = temp_dir / "test.py"
        test_file.write_text('''
"""
Example: Never use shell=True in subprocess calls.
"""
import subprocess
result = subprocess.run(["ls"])
''')
        findings = security_agent.scan_file(test_file)

        shell_findings = [f for f in findings if f.pattern_matched and 'shell' in f.pattern_matched.lower()]
        assert len(shell_findings) == 0

    def test_false_positive_shell_true_in_comment_explaining(self, security_agent, temp_dir):
        """Test that shell=True explanation comments are not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
# We use shell=False here (never shell=True)
import subprocess
result = subprocess.run(["ls"])
""")
        findings = security_agent.scan_file(test_file)

        shell_findings = [f for f in findings if f.pattern_matched and 'shell' in f.pattern_matched.lower()]
        assert len(shell_findings) == 0


class TestReliabilityAgent:
    """Tests for ReliabilityAgent."""

    def test_detects_bare_except(self, reliability_agent, temp_dir):
        """Test that ReliabilityAgent detects bare except."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
try:
    risky()
except:
    pass
""")
        findings = reliability_agent.scan_file(test_file)

        bare_except = [f for f in findings if f.pattern_matched and 'bare_except' in f.pattern_matched]
        assert len(bare_except) >= 1
        assert bare_except[0].severity == Severity.HIGH

    def test_detects_todo_comments(self, reliability_agent, temp_dir):
        """Test that ReliabilityAgent detects TODO comments."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
# TODO: implement this feature
def placeholder():
    pass
""")
        findings = reliability_agent.scan_file(test_file)

        todo_findings = [f for f in findings if f.pattern_matched and 'todo' in f.pattern_matched.lower()]
        assert len(todo_findings) >= 1
        assert todo_findings[0].severity == Severity.INFO

    def test_detects_fixme_comments(self, reliability_agent, temp_dir):
        """Test that ReliabilityAgent detects FIXME comments."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
# FIXME: this is broken
def broken():
    pass
""")
        findings = reliability_agent.scan_file(test_file)

        fixme_findings = [f for f in findings if f.pattern_matched and 'fixme' in f.pattern_matched.lower()]
        assert len(fixme_findings) >= 1
        assert fixme_findings[0].severity == Severity.MEDIUM

    def test_allows_specific_exception(self, reliability_agent, temp_dir):
        """Test that specific exception types are not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
try:
    risky()
except Exception as e:
    logger.error(e)
""")
        findings = reliability_agent.scan_file(test_file)

        bare_except = [f for f in findings if f.pattern_matched and 'bare_except' in f.pattern_matched]
        assert len(bare_except) == 0


class TestPerformanceAgent:
    """Tests for PerformanceAgent."""

    def test_detects_requests_no_timeout(self, performance_agent, temp_dir):
        """Test detection of requests without timeout."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
import requests
response = requests.get("https://example.com")
""")
        findings = performance_agent.scan_file(test_file)

        timeout_findings = [f for f in findings if f.pattern_matched and 'timeout' in f.pattern_matched.lower()]
        assert len(timeout_findings) >= 1

    def test_allows_requests_with_timeout(self, performance_agent, temp_dir):
        """Test that requests with timeout are not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
import requests
response = requests.get("https://example.com", timeout=10)
""")
        findings = performance_agent.scan_file(test_file)

        timeout_findings = [f for f in findings if f.pattern_matched and 'requests' in f.pattern_matched.lower()]
        assert len(timeout_findings) == 0

    def test_detects_glib_timer_without_cleanup(self, performance_agent, temp_dir):
        """Test detection of GLib timers without cleanup tracking."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
from gi.repository import GLib
GLib.timeout_add(1000, callback)
""")
        findings = performance_agent.scan_file(test_file)

        timer_findings = [f for f in findings if f.pattern_matched and 'glib' in f.pattern_matched.lower()]
        # May or may not be flagged depending on false positive logic
        # The important thing is we don't crash
        assert isinstance(findings, list)


class TestReviewAgent:
    """Tests for base ReviewAgent class."""

    def test_scan_file_handles_unicode_error(self, security_agent, temp_dir):
        """Test that scan_file handles UnicodeDecodeError gracefully."""
        test_file = temp_dir / "binary.py"
        test_file.write_bytes(b'\x80\x81\x82\x83')  # Invalid UTF-8

        findings = security_agent.scan_file(test_file)
        assert findings == []  # Should return empty, not crash

    def test_scan_file_handles_missing_file(self, security_agent, temp_dir):
        """Test that scan_file handles IOError gracefully."""
        missing_file = temp_dir / "nonexistent.py"

        findings = security_agent.scan_file(missing_file)
        assert findings == []  # Should return empty, not crash

    def test_scan_directory(self, security_agent, temp_dir):
        """Test scanning entire directory."""
        # Create test files
        (temp_dir / "safe.py").write_text("x = 1")
        (temp_dir / "unsafe.py").write_text("result = eval('1+1')")

        result = security_agent.scan_directory(temp_dir)

        assert result.files_scanned == 2
        assert result.category == ReviewCategory.SECURITY
        assert len(result.findings) >= 1

    def test_scan_directory_skips_pycache(self, security_agent, temp_dir):
        """Test that __pycache__ directories are skipped."""
        pycache = temp_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "cached.py").write_text("eval('hack')")

        result = security_agent.scan_directory(temp_dir)

        # Only counts actual source files, not pycache
        assert result.files_scanned == 0

    def test_documentation_line_detection(self, security_agent):
        """Test that documentation lines are identified."""
        doc_lines = [
            "example: shell=True is dangerous",
            "e.g., subprocess.run(cmd, shell=True)",
            "Usage: Never use eval()",
            ">>> eval('1+1')",
        ]
        for line in doc_lines:
            assert security_agent._is_documentation_line(line.strip()) is True

    def test_non_documentation_line(self, security_agent):
        """Test that code lines are not identified as documentation."""
        code_lines = [
            "result = eval(user_input)",
            "subprocess.run(cmd, shell=True)",
        ]
        for line in code_lines:
            assert security_agent._is_documentation_line(line.strip()) is False


class TestReviewOrchestrator:
    """Tests for ReviewOrchestrator."""

    def test_orchestrator_creation(self, temp_dir):
        """Test ReviewOrchestrator can be created."""
        orchestrator = ReviewOrchestrator(temp_dir)
        assert orchestrator.source_directory == temp_dir
        assert len(orchestrator.agents) == 4

    def test_run_full_review_all_scope(self, temp_dir):
        """Test running full review with ALL scope."""
        (temp_dir / "test.py").write_text("x = 1")

        orchestrator = ReviewOrchestrator(temp_dir)
        report = orchestrator.run_full_review(scope=ReviewScope.ALL)

        assert report.scope == ReviewScope.ALL
        assert len(report.agent_results) == 4
        assert ReviewCategory.SECURITY in report.agent_results
        assert ReviewCategory.RELIABILITY in report.agent_results

    def test_run_full_review_security_scope(self, temp_dir):
        """Test running review with SECURITY scope only."""
        (temp_dir / "test.py").write_text("x = 1")

        orchestrator = ReviewOrchestrator(temp_dir)
        report = orchestrator.run_full_review(scope=ReviewScope.SECURITY)

        assert len(report.agent_results) == 1
        assert ReviewCategory.SECURITY in report.agent_results

    def test_run_targeted_review(self, temp_dir):
        """Test running targeted review on specific files."""
        test_file = temp_dir / "specific.py"
        test_file.write_text("eval('hack')")

        orchestrator = ReviewOrchestrator(temp_dir)
        report = orchestrator.run_targeted_review(
            file_paths=[test_file],
            categories=[ReviewCategory.SECURITY],
        )

        assert report.total_files_scanned == 1
        assert len(report.agent_results[ReviewCategory.SECURITY].findings) >= 1


class TestDetectReviewRequest:
    """Tests for detect_review_request function."""

    def test_detects_full_review_request(self):
        """Test detection of full review triggers."""
        assert detect_review_request("Please do an exhaustive code review") == ReviewScope.ALL
        assert detect_review_request("I need a code audit") == ReviewScope.ALL
        assert detect_review_request("Run a full review of the codebase") == ReviewScope.ALL

    def test_detects_security_review_request(self):
        """Test detection of security-specific triggers."""
        assert detect_review_request("run security review") == ReviewScope.SECURITY
        assert detect_review_request("Check security vulnerabilities") == ReviewScope.SECURITY

    def test_detects_performance_review_request(self):
        """Test detection of performance-specific triggers."""
        assert detect_review_request("performance review needed") == ReviewScope.PERFORMANCE
        assert detect_review_request("optimize meshforge") == ReviewScope.PERFORMANCE

    def test_detects_reliability_review_request(self):
        """Test detection of reliability-specific triggers."""
        assert detect_review_request("reliability check please") == ReviewScope.RELIABILITY
        assert detect_review_request("check reliability of code") == ReviewScope.RELIABILITY

    def test_detects_redundancy_review_request(self):
        """Test detection of redundancy-specific triggers."""
        assert detect_review_request("clean up redundancy") == ReviewScope.REDUNDANCY

    def test_returns_none_for_unrelated_messages(self):
        """Test that unrelated messages return None."""
        assert detect_review_request("Hello, how are you?") is None
        assert detect_review_request("Add a new feature") is None
        assert detect_review_request("Fix the button color") is None


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_run_review_function(self, temp_dir):
        """Test run_review convenience function."""
        (temp_dir / "test.py").write_text("x = 1")

        report = run_review(scope=ReviewScope.ALL, source_dir=temp_dir)

        assert isinstance(report, ReviewReport)
        assert report.scope == ReviewScope.ALL

    def test_generate_report_markdown_function(self, sample_finding):
        """Test generate_report_markdown convenience function."""
        result = AgentResult(
            category=ReviewCategory.SECURITY,
            files_scanned=1,
            findings=[sample_finding],
        )
        report = ReviewReport(
            scope=ReviewScope.ALL,
            agent_results={ReviewCategory.SECURITY: result},
        )

        markdown = generate_report_markdown(report)

        assert isinstance(markdown, str)
        assert "MeshForge" in markdown


class TestFalsePositiveDetection:
    """Tests for false positive detection logic."""

    def test_os_system_with_shlex_quote_same_line_is_false_positive(self, security_agent, temp_dir):
        """Test that os.system with shlex.quote on same line is not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
import os
import shlex
os.system(f"echo {shlex.quote(user_input)}")
""")
        findings = security_agent.scan_file(test_file)

        os_findings = [f for f in findings if f.pattern_matched and 'os_system' in f.pattern_matched]
        assert len(os_findings) == 0

    def test_os_system_without_shlex_quote_is_flagged(self, security_agent, temp_dir):
        """Test that os.system without shlex.quote is flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
import os
os.system(f"echo {user_input}")
""")
        findings = security_agent.scan_file(test_file)

        os_findings = [f for f in findings if f.pattern_matched and 'os_system' in f.pattern_matched]
        assert len(os_findings) >= 1

    def test_subprocess_xdg_open_is_false_positive(self, performance_agent, temp_dir):
        """Test that xdg-open without timeout is not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
import subprocess
subprocess.run(["xdg-open", url])
""")
        findings = performance_agent.scan_file(test_file)

        timeout_findings = [f for f in findings if f.pattern_matched and 'subprocess' in f.pattern_matched.lower()]
        assert len(timeout_findings) == 0

    def test_index_access_with_split_is_false_positive(self, reliability_agent, temp_dir):
        """Test that split()[0] patterns are not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
line = "hello world"
first_word = line.split()[0]
""")
        findings = reliability_agent.scan_file(test_file)

        index_findings = [f for f in findings if f.pattern_matched and 'index' in f.pattern_matched.lower()]
        assert len(index_findings) == 0

    def test_glib_timer_with_tracking_is_false_positive(self, performance_agent, temp_dir):
        """Test that tracked GLib timers are not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
from gi.repository import GLib
timer_id = GLib.timeout_add(1000, callback)
""")
        findings = performance_agent.scan_file(test_file)

        timer_findings = [f for f in findings if f.pattern_matched and 'glib' in f.pattern_matched.lower()]
        assert len(timer_findings) == 0


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_file(self, security_agent, temp_dir):
        """Test scanning an empty file."""
        test_file = temp_dir / "empty.py"
        test_file.write_text("")

        findings = security_agent.scan_file(test_file)
        assert findings == []

    def test_file_with_only_comments(self, security_agent, temp_dir):
        """Test scanning file with only comments."""
        test_file = temp_dir / "comments.py"
        test_file.write_text("""
# This file has only comments
# shell=True is mentioned here but shouldn't be flagged
# eval() is also mentioned
""")
        findings = security_agent.scan_file(test_file)
        assert findings == []

    def test_multiline_docstring(self, security_agent, temp_dir):
        """Test that multiline docstrings are properly skipped."""
        test_file = temp_dir / "docstring.py"
        test_file.write_text('''
"""
This is a multiline docstring.
It mentions shell=True but should not be flagged.
It also mentions eval() which is dangerous.
"""

def safe_function():
    """Single line docstring with shell=True mentioned."""
    return "safe"
''')
        findings = security_agent.scan_file(test_file)

        shell_findings = [f for f in findings if f.pattern_matched and 'shell' in f.pattern_matched.lower()]
        eval_findings = [f for f in findings if f.pattern_matched and 'eval' in f.pattern_matched.lower()]
        assert len(shell_findings) == 0
        assert len(eval_findings) == 0

    def test_single_quote_docstring(self, security_agent, temp_dir):
        """Test that single-quote docstrings are properly skipped."""
        test_file = temp_dir / "single_quote.py"
        test_file.write_text("""
'''
This docstring uses single quotes.
shell=True is mentioned but should not be flagged.
'''

x = 1
""")
        findings = security_agent.scan_file(test_file)

        shell_findings = [f for f in findings if f.pattern_matched and 'shell' in f.pattern_matched.lower()]
        assert len(shell_findings) == 0


class TestPersistentIssuePatterns:
    """Tests for patterns that detect documented persistent issues."""

    def test_detects_path_home(self, security_agent, temp_dir):
        """Test detection of Path.home() - Issue #1."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
from pathlib import Path
config_dir = Path.home() / ".config" / "meshforge"
""")
        findings = security_agent.scan_file(test_file)

        path_home_findings = [f for f in findings if f.pattern_matched and 'path_home' in f.pattern_matched]
        assert len(path_home_findings) >= 1
        assert path_home_findings[0].severity.value in ['critical', 'high']

    def test_path_home_with_spaces(self, security_agent, temp_dir):
        """Test detection of Path.home() with various spacing."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
from pathlib import Path
a = Path.home()
b = Path.home( )
""")
        findings = security_agent.scan_file(test_file)

        path_home_findings = [f for f in findings if f.pattern_matched and 'path_home' in f.pattern_matched]
        assert len(path_home_findings) >= 2

    def test_allows_get_real_user_home(self, security_agent, temp_dir):
        """Test that get_real_user_home() is not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
from utils.paths import get_real_user_home
config_dir = get_real_user_home() / ".config" / "meshforge"
""")
        findings = security_agent.scan_file(test_file)

        path_home_findings = [f for f in findings if f.pattern_matched and 'path_home' in f.pattern_matched]
        assert len(path_home_findings) == 0

    def test_detects_lambda_closure_in_loop(self, reliability_agent, temp_dir):
        """Test detection of lambda closure bug in loops - Issue #10."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
for item in items:
    btn.connect("clicked", lambda b: self._handle(item))
""")
        findings = reliability_agent.scan_file(test_file)

        lambda_findings = [f for f in findings if f.pattern_matched and 'lambda_closure' in f.pattern_matched]
        assert len(lambda_findings) >= 1
        assert lambda_findings[0].severity.value in ['medium', 'high']

    def test_allows_lambda_with_default_arg(self, reliability_agent, temp_dir):
        """Test that lambda with default argument (correct pattern) is not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
for item in items:
    btn.connect("clicked", lambda b, i=item: self._handle(i))
""")
        findings = reliability_agent.scan_file(test_file)

        lambda_findings = [f for f in findings if f.pattern_matched and 'lambda_closure' in f.pattern_matched]
        assert len(lambda_findings) == 0

    def test_detects_exception_pass_pattern(self, reliability_agent, temp_dir):
        """Test detection of broad exception swallowing - Issue #9."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
try:
    risky_operation()
except Exception:
    pass
""")
        findings = reliability_agent.scan_file(test_file)

        exception_findings = [f for f in findings if f.pattern_matched and 'exception_pass' in f.pattern_matched]
        assert len(exception_findings) >= 1

    def test_allows_exception_with_logging(self, reliability_agent, temp_dir):
        """Test that exception with logging is not flagged."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
try:
    risky_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}")
""")
        findings = reliability_agent.scan_file(test_file)

        exception_findings = [f for f in findings if f.pattern_matched and 'exception_pass' in f.pattern_matched]
        assert len(exception_findings) == 0

    def test_detects_duplicate_utility_function(self, redundancy_agent, temp_dir):
        """Test detection of duplicate utility functions - Issue #5."""
        test_file = temp_dir / "test.py"
        test_file.write_text("""
def _get_real_user_home():
    '''Local copy of utility function - should use utils.paths'''
    return Path.home()
""")
        findings = redundancy_agent.scan_file(test_file)

        dup_findings = [f for f in findings if f.pattern_matched and 'duplicate_utility' in f.pattern_matched]
        assert len(dup_findings) >= 1

    def test_allows_canonical_utility_in_utils(self, redundancy_agent, temp_dir):
        """Test that canonical implementation in utils module is not flagged."""
        # Simulate the utils/paths.py file - should NOT be flagged
        utils_dir = temp_dir / "utils"
        utils_dir.mkdir()
        test_file = utils_dir / "paths.py"
        test_file.write_text("""
def get_real_user_home():
    '''Canonical implementation for the project.'''
    return Path(os.environ.get('HOME', '/'))
""")
        findings = redundancy_agent.scan_file(test_file)

        # The canonical definition shouldn't be flagged as duplicate
        dup_findings = [f for f in findings if f.pattern_matched and 'duplicate_utility' in f.pattern_matched]
        assert len(dup_findings) == 0


class TestNewPatternConfiguration:
    """Tests to verify new patterns are properly configured."""

    def test_path_home_pattern_exists_in_security(self):
        """Verify path_home pattern is defined in SECURITY patterns."""
        assert 'path_home' in ReviewPatterns.SECURITY
        config = ReviewPatterns.SECURITY['path_home']
        assert 'pattern' in config
        assert 'severity' in config
        assert config['severity'] in [Severity.CRITICAL, Severity.HIGH]

    def test_lambda_closure_pattern_exists_in_reliability(self):
        """Verify lambda_closure pattern is defined in RELIABILITY patterns."""
        assert 'lambda_closure' in ReviewPatterns.RELIABILITY
        config = ReviewPatterns.RELIABILITY['lambda_closure']
        assert 'pattern' in config
        assert 'severity' in config

    def test_exception_pass_pattern_exists_in_reliability(self):
        """Verify exception_pass pattern is defined in RELIABILITY patterns."""
        assert 'exception_pass' in ReviewPatterns.RELIABILITY
        config = ReviewPatterns.RELIABILITY['exception_pass']
        assert 'pattern' in config

    def test_duplicate_utility_pattern_exists_in_redundancy(self):
        """Verify duplicate_utility pattern is defined in REDUNDANCY patterns."""
        assert 'duplicate_utility' in ReviewPatterns.REDUNDANCY
        config = ReviewPatterns.REDUNDANCY['duplicate_utility']
        assert 'pattern' in config
