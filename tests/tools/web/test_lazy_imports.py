from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_tools_setup_and_registry_do_not_require_bs4_at_import_time() -> None:
    code = r"""
import builtins

original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == 'bs4' or name.startswith('bs4.'):
        raise ModuleNotFoundError("No module named 'bs4'")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import

import agent.tools.registry
import agent.tools.web.registry
import agent.ui.tools_setup
from agent.tools.web.config import get_extract_backend, is_backend_available
assert get_extract_backend() == ''
assert not is_backend_available('content_extractor')
print('ok')
"""
    env = os.environ.copy()
    repo_root = str(Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
