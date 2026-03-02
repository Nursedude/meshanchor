"""
Review pattern definitions for MeshForge Auto-Review System.

Centralized pattern dictionaries used by ReviewAgent to scan for
security, redundancy, performance, and reliability issues.

Separated from auto_review.py for maintainability and reuse by the linter.

Usage:
    from utils.review_patterns import ReviewPatterns, Severity
"""

from enum import Enum


class Severity(Enum):
    """Issue severity levels"""
    CRITICAL = "critical"  # Must fix before merge
    HIGH = "high"          # Should fix this sprint
    MEDIUM = "medium"      # Plan fix next sprint
    LOW = "low"            # Consider for backlog
    INFO = "info"          # Informational only


class ReviewPatterns:
    """
    Centralized review patterns for each agent category.

    These patterns align with the MeshForge Auto-Review Principles.
    """

    # Security patterns (Priority order: CRITICAL, HIGH, MEDIUM)
    SECURITY = {
        # CRITICAL - Command/Code injection
        'shell_true': {
            'pattern': r'shell\s*=\s*True',
            'severity': Severity.HIGH,
            'issue': 'Command injection risk',
            'recommendation': 'Use argument list instead of shell=True',
            'auto_fixable': True,
        },
        'eval_call': {
            'pattern': r'\beval\s*\(',
            'severity': Severity.CRITICAL,
            'issue': 'Code injection via eval()',
            'recommendation': 'Use ast.literal_eval() for data, avoid eval entirely',
            'auto_fixable': False,
        },
        'exec_call': {
            'pattern': r'\bexec\s*\(',
            'severity': Severity.CRITICAL,
            'issue': 'Code execution via exec()',
            'recommendation': 'Remove exec() or use safer alternatives',
            'auto_fixable': False,
        },
        'os_system': {
            'pattern': r'os\.system\s*\(',
            'severity': Severity.HIGH,
            'issue': 'Command injection via os.system()',
            'recommendation': 'Use subprocess.run() with shell=False',
            'auto_fixable': False,
        },
        # HIGH - Data exposure
        'hardcoded_password': {
            'pattern': r'password\s*=\s*["\'][^"\']+["\']',
            'severity': Severity.HIGH,
            'issue': 'Hardcoded password',
            'recommendation': 'Use environment variable or secure config',
            'auto_fixable': False,
        },
        'hardcoded_api_key': {
            'pattern': r'api_key\s*=\s*["\'][^"\']+["\']',
            'severity': Severity.HIGH,
            'issue': 'Hardcoded API key',
            'recommendation': 'Use environment variable or secure config',
            'auto_fixable': False,
        },
        # MEDIUM - Deserialization
        'pickle_load': {
            'pattern': r'pickle\.load\s*\(',
            'severity': Severity.MEDIUM,
            'issue': 'Unsafe deserialization',
            'recommendation': 'Use JSON or validate pickle source',
            'auto_fixable': False,
        },
        'yaml_unsafe': {
            'pattern': r'yaml\.load\s*\([^)]*\)\s*(?!.*Loader)',
            'severity': Severity.MEDIUM,
            'issue': 'YAML load without safe Loader',
            'recommendation': 'Use yaml.safe_load() or specify Loader=yaml.SafeLoader',
            'auto_fixable': True,
        },
        # Issue #1: Path.home() returns /root with sudo
        'path_home': {
            'pattern': r'Path\.home\s*\(\s*\)',
            'severity': Severity.HIGH,
            'issue': 'Path.home() returns /root with sudo (MF001)',
            'recommendation': 'Use get_real_user_home() from utils.paths',
            'auto_fixable': False,
        },
    }

    # Redundancy patterns
    REDUNDANCY = {
        'console_instantiation': {
            'pattern': r'Console\s*\(\s*\)',
            'severity': Severity.LOW,
            'issue': 'Multiple Console instances',
            'recommendation': 'Use singleton from utils.console',
            'auto_fixable': False,
        },
        'logger_setup': {
            'pattern': r'logging\.getLogger\s*\([^)]*\)',
            'severity': Severity.LOW,
            'issue': 'Duplicate logger setup',
            'recommendation': 'Use get_logger() from utils.logging_config',
            'auto_fixable': False,
        },
        'check_root_function': {
            'pattern': r'def\s+check_root\s*\(',
            'severity': Severity.MEDIUM,
            'issue': 'Duplicate check_root function',
            'recommendation': 'Use require_root() from utils.system',
            'auto_fixable': False,
        },
        # Issue #5: Duplicate utility functions
        'duplicate_utility': {
            'pattern': r'def\s+_?get_real_user_home\s*\(',
            'severity': Severity.MEDIUM,
            'issue': 'Duplicate utility function (Issue #5)',
            'recommendation': 'Use get_real_user_home() from utils.paths',
            'auto_fixable': False,
        },
    }

    # Performance patterns
    PERFORMANCE = {
        'subprocess_no_timeout': {
            'pattern': r'subprocess\.(run|Popen|call)\s*\([^)]*\)(?!.*timeout)',
            'severity': Severity.MEDIUM,
            'issue': 'Subprocess without timeout',
            'recommendation': 'Add timeout parameter (e.g., timeout=30)',
            'auto_fixable': False,
        },
        'requests_no_timeout': {
            'pattern': r'requests\.(get|post|put|delete)\s*\([^)]*\)(?!.*timeout)',
            'severity': Severity.MEDIUM,
            'issue': 'HTTP request without timeout',
            'recommendation': 'Add timeout parameter (e.g., timeout=10)',
            'auto_fixable': False,
        },
        'string_concat_loop': {
            'pattern': r'for\s+[^:]+:\s*[^=]+=\s*[^=]+\+\s*["\']',
            'severity': Severity.LOW,
            'issue': 'String concatenation in loop',
            'recommendation': 'Use list.append() and "".join()',
            'auto_fixable': False,
        },
    }

    # Reliability patterns
    RELIABILITY = {
        'bare_except': {
            'pattern': r'except\s*:',
            'severity': Severity.HIGH,
            'issue': 'Bare except catches SystemExit',
            'recommendation': 'Catch specific exceptions (e.g., except Exception as e:)',
            'auto_fixable': True,
        },
        'index_no_check': {
            'pattern': r'\[\s*0\s*\]',  # Simplified - real check needs context
            'severity': Severity.LOW,
            'issue': 'Index access may fail on empty',
            'recommendation': 'Check length before indexing or use .get()',
            'auto_fixable': False,
        },
        'todo_comment': {
            'pattern': r'#\s*TODO',
            'severity': Severity.INFO,
            'issue': 'Unfinished code (TODO)',
            'recommendation': 'Complete or create issue for tracking',
            'auto_fixable': False,
        },
        'fixme_comment': {
            'pattern': r'#\s*FIXME',
            'severity': Severity.MEDIUM,
            'issue': 'Known issue (FIXME)',
            'recommendation': 'Address the identified issue',
            'auto_fixable': False,
        },
        # Issue #9: Broad exception swallowing
        'exception_pass': {
            'pattern': r'except\s+Exception\s*:\s*$',
            'severity': Severity.MEDIUM,
            'issue': 'Exception swallowed without handling (Issue #9)',
            'recommendation': 'Log the exception or handle it meaningfully',
            'auto_fixable': False,
        },
        # Issue #10: Lambda closure bug in loops
        'lambda_closure': {
            'pattern': r'lambda\s+\w+\s*:\s*\S+\([^)]*\b\w+\b[^)]*\)',
            'severity': Severity.MEDIUM,
            'issue': 'Potential lambda closure bug in loop (Issue #10)',
            'recommendation': 'Use default argument: lambda b, item=item: ...',
            'auto_fixable': False,
        },
    }
