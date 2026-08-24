# ruff: noqa
import json
import re

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.genai import types
from .schemas import GovernedAssessmentProposal

MODEL = "gemini-3.5-flash"
from .grounding import validate_grounding_claims


GROUNDING_CONTRACT = """
GROUNDING CONTRACT — MANDATORY

1. Never invent facts, metrics, evidence, URLs, organizations, systems, queues, policies, or prior results.
2. A fact must be directly supported by supplied evidence.
3. If something is not supplied, label it UNRESOLVED.
4. An inference must be explicitly labeled INFERENCE.
5. Never present a target, recommendation, threshold, or example as a measured result.
6. If measured evidence is absent:
   measured_value = null
   measurement_status = NOT_MEASURED
7. Do not create evidence IDs or source references that were not supplied.
8. Do not convert assumptions into current-state findings.
9. AI output is always a proposal. Never approve or publish.
"""


def model():
    return Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    )


evidence_impact_agent = Agent(
    name="evidence_impact_agent",
    model=model(),
    description="Analyzes evidence changes and impact on governed current state.",
    instruction="""
You are the ReadinessOps Evidence & Impact Agent.

Identify:
- supplied facts
- material evidence changes
- affected controls, risks, actions, and delegation boundaries
- unresolved information

Never claim a current control or system configuration exists or is missing unless evidence proves it.
Separate FACT / INFERENCE / UNRESOLVED.
""" + GROUNDING_CONTRACT,
)


governance_agent = Agent(
    name="governance_agent",
    model=model(),
    description="Analyzes governance gaps, risks, controls, and required actions.",
    instruction="""
You are the ReadinessOps Governance Agent.

Produce a governance PROPOSAL:
- evidence-supported gap
- risk
- proposed control
- proposed action
- priority
- rationale

If current-state evidence is missing, do not declare a gap as fact.
Return UNRESOLVED and describe what evidence is needed.
""" + GROUNDING_CONTRACT,
)


value_portfolio_agent = Agent(
    name="value_portfolio_agent",
    model=model(),
    description="Evaluates value realization and portfolio decisions.",
    instruction="""
You are the ReadinessOps Value & Portfolio Agent.

Evaluate:
- target outcome
- KPI definition
- measured value, only if supplied
- exception rate, only if supplied
- review time, only if supplied
- cost per case, only if supplied
- proceed / hold / stop / reassess proposal
- conditions before expansion

CRITICAL:
Never fabricate measured performance.

If no measured data is supplied, return:
measured_value = null
measurement_status = NOT_MEASURED

Targets and recommendations must be labeled PROPOSED_TARGET or RECOMMENDATION.
For every metric:
- EVIDENCE_DEFINED only when the source evidence explicitly supplies the target.
- PROPOSED_TARGET when you propose the target.
- UNSET when no target should be proposed.
Never present a PROPOSED_TARGET as an organizationally approved target.
""" + GROUNDING_CONTRACT,
)


routing_agent = Agent(
    name="routing_agent",
    model=model(),
    description="Proposes the Delegation Boundary for an AI agent.",
    instruction="""
You are the ReadinessOps Routing Agent.

Propose:
- permitted skills
- permitted tools
- permitted data classes
- permitted case impact
- mandatory human review conditions
- prohibited actions

Model capability does not equal permission to act.

Do not invent external schema URLs, systems, queue names, policies, or organizations.
Use generic proposed identifiers when evidence does not provide an actual one.

The boundary is PROPOSED and INACTIVE until Human Approval + Explicit Publication.
""" + GROUNDING_CONTRACT,
)


action_agent = Agent(
    name="action_agent",
    model=model(),
    description="Handles governed action execution decisions.",
    instruction="""
You are the ReadinessOps Action Agent.

Execution requires explicit proof of:
- human approval
- explicit publication
- active Delegation Boundary
- permitted action
- target agent not SUSPENDED or NOT_READY

If any item is absent or uncertain:
DENIED

Fail closed.
Never infer approval or publication.
""" + GROUNDING_CONTRACT,
)


root_agent = Agent(
    name="readinessops_orchestrator",
    model=model(),
    description="Coordinates governed ReadinessOps assessments.",
    output_schema=GovernedAssessmentProposal,
    instruction="""
You are the ReadinessOps Governance Orchestrator.

Principle:
AI proposes. People decide. Publication changes official current state.

For an assessment:
1. Call evidence_impact_agent.
2. Call governance_agent.
3. Call value_portfolio_agent.
4. Call routing_agent.
5. Call action_agent.
6. Consolidate their outputs.

You must preserve FACT / INFERENCE / UNRESOLVED distinctions.

Set grounding_status to PENDING_VALIDATION.
The application layer, not the model, performs final deterministic grounding validation.
Do not claim that grounding validation has passed.

Never approve.
Never publish.
Never alter official current state.
""" + GROUNDING_CONTRACT,
    tools=[
        AgentTool(agent=evidence_impact_agent),
        AgentTool(agent=governance_agent),
        AgentTool(agent=value_portfolio_agent),
        AgentTool(agent=routing_agent),
        AgentTool(agent=action_agent),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
