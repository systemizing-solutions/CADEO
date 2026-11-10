"""Run a headless UI editor smoke test with mocked HTTP responses when available.

The real Flask endpoints are independently exercised by dependency-enabled
integration tests; this test targets client form state and registry consistency.
"""
from pathlib import Path
import importlib.util
import subprocess
import sys

import pytest


@pytest.mark.skipif(importlib.util.find_spec('playwright') is None,
                    reason='Requires Playwright and Chromium')
def test_model_selector_adds_all_four_authoritative_registries():
    script=Path(__file__).with_name('browser_multi_model_smoke.py')
    result=subprocess.run([sys.executable,str(script)],capture_output=True,text=True,timeout=40)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    assert 'BROWSER SMOKE PASS' in result.stdout
