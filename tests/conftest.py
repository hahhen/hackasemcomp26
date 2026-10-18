import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"  # never load real credentials/config in tests

# Tests must never reach a real LLM or a real iFood account. The provider chain
# is chosen by ENVIRONMENT (local|remote), so the only reliable way to keep the
# suite hermetic is to switch the model off in each place that could call one.
os.environ["MESA_MODE"] = "mock"
os.environ["MESA_OBSERVABILITY"] = "false"
os.environ["MESA_USE_LLM"] = "false"  # intent extraction: keywords only
os.environ["MESA_RECOMMEND_LLM"] = "false"  # restaurant searches: keywords only
os.environ["MESA_CHAT_LLM"] = "false"  # chat agent: fallback path only
os.environ.pop("MESA_PERSIST", None)
# Belt and braces: if a call slips through it fails in 2s instead of retrying.
os.environ["LLM_MAX_RETRIES"] = "0"
os.environ["LLM_TIMEOUT"] = "2"
os.environ["MESA_CHAT_TIMEOUT"] = "10"

import pytest

from agent.mesa import gateway
from agent.mesa.mock_data import MockGateway
from agent.mesa.store import store


@pytest.fixture(autouse=True)
def fresh_world():
    gateway.set_gateway(MockGateway())
    store.sessions.clear()
    yield
    gateway.set_gateway(None)
    store.sessions.clear()
