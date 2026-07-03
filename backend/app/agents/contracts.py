from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class AgentName(str, Enum):
    market_structure_analyst = "market_structure_analyst"
    liquidity_analyst = "liquidity_analyst"
    momentum_analyst = "momentum_analyst"
    bull_researcher = "bull_researcher"
    bear_researcher = "bear_researcher"
    risk_guardian = "risk_guardian"
    fomo_gatekeeper = "fomo_gatekeeper"


class Stance(str, Enum):
    supportive = "supportive"
    caution = "caution"
    neutral = "neutral"
    risk_first = "risk_first"
    fomo_warning = "fomo_warning"


class AgentInput(BaseModel):
    report_id: UUID
    snapshot_id: UUID
    symbol: str
    timeframe: str
    raw_json: dict
    memories: list[dict] = Field(default_factory=list)


class AgentResult(BaseModel):
    agent: AgentName
    stance: Stance
    confidence: float
    raw_json: dict
    text_output: str


class ResearchRunInput(BaseModel):
    symbol: str
    timeframe: str = "4h"
    mode: str = "entry_review"


class ResearchRunOutput(BaseModel):
    research_run_id: str
    symbol: str
    timeframe: str
    entry_score: int
    fomo_index: int
    state_label: str
    final_action_label: str
    summary: str
    agents: list[dict]
