"""Pytest configuration ensuring fast, credential-less unit test isolation."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_gcp_environment(monkeypatch):
    """Prevents unit tests from probing GCE metadata servers or real ADC credentials."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project-unit")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "True")
    with patch("google.auth.default", return_value=(MagicMock(), "test-project-unit")):
        yield
