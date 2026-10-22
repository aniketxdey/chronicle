from __future__ import annotations

import os
import tempfile

os.environ["CHRONICLE_HOME"] = tempfile.mkdtemp(prefix="chronicle-test-")
os.environ["CHRONICLE_DISABLE_JOBS"] = "1"
os.environ["CHRONICLE_DISABLE_CAPTURE"] = "1"
os.environ["CHRONICLE_DEMO"] = "1"
os.environ["CHRONICLE_LLM"] = "off"
os.environ.pop("CHRONICLE_TOKEN", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from chronicle.main import app  # noqa: E402

_client = TestClient(app)
_client.__enter__()


@pytest.fixture
def client() -> TestClient:
    return _client
