"""
tests/test_security.py
========================
Security regression tests.
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAPIKeySecurity:
    def test_gemini_key_not_in_url_params(self):
        """Verify Gemini API key is sent via headers, not URL params."""
        source = Path(__file__).parent.parent / "main.py"
        content = source.read_text()
        # Should NOT have params={"key": ...} for Gemini
        assert 'params={"key": ai_key}' not in content
        assert 'params={\"key\":' not in content

    def test_cors_not_wildcard(self):
        """Verify CORS is not set to wildcard *."""
        source = Path(__file__).parent.parent / "dashboard" / "api.py"
        content = source.read_text()
        assert 'allow_origins=["*"]' not in content

    def test_command_center_served_by_unified_api(self):
        """Verify CC routes are served by unified API, not a separate server."""
        source = Path(__file__).parent.parent / "main.py"
        content = source.read_text()
        # CC should be served by unified API on :8000/cc/*, not separate :8765 server
        assert 'CC routes served by unified API' in content
        # The old embedded server on 8765 should be removed
        assert 'port=8765' not in content


class TestGitignore:
    def test_gitignore_exists(self):
        gitignore = Path(__file__).parent.parent / ".gitignore"
        assert gitignore.exists()

    def test_gitignore_covers_env(self):
        gitignore = Path(__file__).parent.parent / ".gitignore"
        content = gitignore.read_text()
        assert ".env" in content


class TestExceptions:
    def test_hard_gate_error_exists(self):
        from exceptions import HardGateError, SoftGateError, NZTGateError
        assert issubclass(HardGateError, NZTGateError)
        assert issubclass(SoftGateError, NZTGateError)

    def test_no_except_pass_in_main(self):
        """Verify main.py has minimal bare 'except Exception: pass' blocks.

        Allowed exceptions:
          - dotenv loader (optional dependency, safe to skip)
          - heartbeat loop (non-critical, fire-and-forget)
        """
        source = Path(__file__).parent.parent / "main.py"
        content = source.read_text()
        import re
        matches = re.findall(r'except Exception:\s*\n\s*pass', content)
        # Max 2 allowed: dotenv loader + heartbeat loop (both are safe fire-and-forget)
        assert len(matches) <= 2, f"Found {len(matches)} 'except Exception: pass' in main.py (max 2 allowed)"
