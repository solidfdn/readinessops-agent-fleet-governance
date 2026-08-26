# ruff: noqa
import json
import re

from google.adk.agents import Agent
from google.adk.agents.parallel_agent import ParallelAgent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.apps import App
from google.adk.models import Gemini
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


governance_agent = Agent(
    name="governance_agent",
    model=model(),
    output_key="governance_analysis",
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
    output_key="value_portfolio_analysis",
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
    output_key="routing_analysis",
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


parallel_reassessment = ParallelAgent(
    name="parallel_reassessment",
    description=(
        "Runs independent governance, value/portfolio, and routing analysis "
        "concurrently after evidence impact has already been established."
    ),
    sub_agents=[
        governance_agent,
        value_portfolio_agent,
        routing_agent,
    ],
)

synthesis_agent = Agent(
    name="reassessment_synthesizer",
    model=model(),
    description=(
        "Synthesizes parallel specialist analyses into one governed "
        "ReadinessOps reassessment proposal."
    ),
    output_schema=GovernedAssessmentProposal,
    output_key="governed_assessment_proposal",
    instruction="""
You are the final ReadinessOps Reassessment Synthesizer.

The Evidence Worker has ALREADY completed evidence-impact analysis and supplied
the result in the user request. Do not repeat evidence-impact analysis.

Use the original supplied:
- PUBLISHED CURRENT
- NEW EVIDENCE
- EVIDENCE IMPACT

Also use these parallel specialist results:

GOVERNANCE ANALYSIS:
{governance_analysis}

VALUE / PORTFOLIO ANALYSIS:
{value_portfolio_analysis}

ROUTING ANALYSIS:
{routing_analysis}

Produce exactly one GovernedAssessmentProposal.

Required semantics:
- schema_version = readinessops.google.v1
- proposal_status = REVIEW_REQUIRED
- publication_status = NOT_PUBLISHED
- AI proposes only.
- Preserve FACT / INFERENCE / UNRESOLVED distinctions.
- Never invent metrics, systems, URLs, queues, policies, or evidence.
- Delegation Boundary remains PROPOSED and INACTIVE.
- Human approval alone does not publish.
- Never reactivate an agent.
- Never execute a protected action.
- The action field describes the proposed or denied action implication only;
  it never authorizes execution.
- grounding_status = PENDING_VALIDATION.
- The application layer performs final deterministic grounding validation.

Never approve.
Never publish.
Never alter official current state.
""" + GROUNDING_CONTRACT,
)

root_agent = SequentialAgent(
    name="readinessops_reassessment_workflow",
    description=(
        "Parallel specialist reassessment followed by schema-constrained "
        "governance synthesis."
    ),
    sub_agents=[
        parallel_reassessment,
        synthesis_agent,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
