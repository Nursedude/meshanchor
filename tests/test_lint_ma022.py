"""Tests for the MA022 shell-installer pip/apt-hygiene rule in scripts/lint.py.

Ported from MeshForge's test_lint_mf022.py. Pins the install-hardening guard so
the fresh-user failure class (bare pip with no pip-presence check, the
`pip … | tail` exit-code mask, and apt swallowed to /dev/null) can't creep back
into the shell installers. The rule: shell scripts must route package installs
through scripts/lib/install_common.sh.
"""
import importlib.util
from pathlib import Path

import pytest

_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


def _check(content: str, rel_path: str = "scripts/x.sh", tmp_path=None):
    p = tmp_path / "x.sh"
    p.write_text(content)
    return lint._check_pip_invocations_in_file(str(p), rel_path)


class TestMA022CatchesBadPatterns:
    def test_bare_pip_install_is_flagged_warning(self, tmp_path):
        issues = _check("#!/bin/bash\npip3 install meshtastic\n", tmp_path=tmp_path)
        assert len(issues) == 1
        assert issues[0].code == "MA022"
        assert issues[0].severity == lint.Severity.WARNING
        assert issues[0].line == 2

    def test_pip_piped_to_tail_is_error(self, tmp_path):
        issues = _check(
            "#!/bin/bash\nsudo -u bob pip3 install --user lxmf rns 2>&1 | tail -5\n",
            tmp_path=tmp_path,
        )
        assert len(issues) == 1
        assert issues[0].severity == lint.Severity.ERROR
        assert "exit code" in issues[0].message

    def test_apt_install_swallowed_is_flagged(self, tmp_path):
        issues = _check(
            "#!/bin/bash\napt-get install -y -qq python3-pip git &>/dev/null\n",
            tmp_path=tmp_path,
        )
        assert len(issues) == 1
        assert issues[0].severity == lint.Severity.WARNING
        assert "apt" in issues[0].message.lower()


class TestMA022AllowsGoodPatterns:
    def test_mf_pip_install_is_clean(self, tmp_path):
        issues = _check(
            "#!/bin/bash\nmf_pip_install python3 --upgrade pip\n", tmp_path=tmp_path
        )
        assert issues == []

    def test_python_m_pip_install_is_clean(self, tmp_path):
        issues = _check(
            '#!/bin/bash\n"$py" -m pip install meshtastic\n', tmp_path=tmp_path
        )
        assert issues == []

    def test_apt_install_without_swallow_is_clean(self, tmp_path):
        issues = _check(
            "#!/bin/bash\nif apt-get install -y -q git; then echo ok; fi\n",
            tmp_path=tmp_path,
        )
        assert issues == []

    def test_pip_install_in_quoted_guidance_string_is_clean(self, tmp_path):
        issues = _check(
            '#!/bin/bash\ncheck_warn "x" "y" \\\n    "Install: pip3 install -r requirements.txt"\n',
            tmp_path=tmp_path,
        )
        assert issues == []

    def test_commented_pip_install_is_clean(self, tmp_path):
        issues = _check("#!/bin/bash\n# pip3 install meshtastic (old way)\n", tmp_path=tmp_path)
        assert issues == []


class TestMA022FileSelection:
    def test_non_shell_extension_exempt(self):
        assert lint._ma022_exempt("scripts/x.py") is True

    def test_shell_file_not_exempt(self):
        assert lint._ma022_exempt("scripts/x.sh") is False

    def test_allowlisted_lib_is_exempt(self):
        assert lint._ma022_exempt("scripts/lib/install_common.sh") is True
        assert "scripts/lib/install_common.sh" in lint.MA022_ALLOWED_FILES


class TestMA022RealTreeIsClean:
    """The shipped install scripts must already be clean (the arc routed them)."""

    def test_full_tree_has_no_ma022_findings(self):
        repo_root = str(Path(__file__).parent.parent)
        issues = lint.check_pip_invocations_full_tree(repo_root=repo_root)
        assert issues == [], (
            "MA022 findings in the tree:\n"
            + "\n".join(f"{i.file}:{i.line} {i.message}" for i in issues)
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
