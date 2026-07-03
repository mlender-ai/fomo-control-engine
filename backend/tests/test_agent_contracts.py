import pytest
from pydantic import ValidationError

from app.agents.contracts import AgentName, AgentResult, Stance


def test_agent_output_schema_validation() -> None:
    result = AgentResult(
        agent=AgentName.fomo_gatekeeper,
        stance=Stance.fomo_warning,
        confidence=78,
        raw_json={"warning_level": "high"},
        text_output="FOMO risk is high.",
    )

    assert result.agent == AgentName.fomo_gatekeeper


def test_malformed_agent_output_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentResult(agent="unknown", stance="bad", confidence=50, raw_json={}, text_output="")
