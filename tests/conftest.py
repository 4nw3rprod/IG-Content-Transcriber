from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# mcp_server resolves the output root at import time; point it at a throwaway
# directory before any test imports the module.
_TEST_OUTPUT_ROOT = Path(tempfile.mkdtemp(prefix="reelrecon-test-outputs-"))
os.environ["REELRECON_OUTPUT_DIR"] = str(_TEST_OUTPUT_ROOT)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def output_root() -> Path:
    return _TEST_OUTPUT_ROOT
