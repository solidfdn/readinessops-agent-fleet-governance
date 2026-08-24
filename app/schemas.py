from typing import Literal
from pydantic import BaseModel, Field


class Metric(BaseModel):
    name: str
    target_value: str | None = None
    target_status: Literal["EVIDENCE_DEFINED", "PROPOSED_TARGET", "UNSET"]
    target_basis: str | None = None
    measured_value: str | None = None
    measurement_status: Literal["MEASURED", "NOT_MEASURED"]


class GovernancePack(BaseModel):
    gaps: list[str]
    risks: list[str]
    proposed_controls: list[str]
    required_actions: list[str]
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    rationale: str


class ValueRealizationPack(BaseModel):
    target_outcomes: list[str]
    metrics: list[Metric]
    value_rationale: str


class ModelRoutingPack(BaseModel):
    delegable_work: list[str]
    human_required_work: list[str]
    routing_rules: list[str]
    rationale: str


class PortfolioPack(BaseModel):
    recommendation: Literal["PROCEED", "HOLD", "STOP", "REASSESS"]
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    conditions_before_expansion: list[str]
    rationale: str


class DelegationBoundary(BaseModel):
    status: Literal["PROPOSED"]
    permitted_skills: list[str]
    permitted_tools: list[str]
    permitted_data_classes: list[str]
    permitted_case_impact: list[str]
    mandatory_human_review_conditions: list[str]
    prohibited_actions: list[str]


class ActionDecision(BaseModel):
    status: Literal["DENIED", "PROPOSED"]
    reason: str


class GovernedAssessmentProposal(BaseModel):
    schema_version: Literal["readinessops.google.v1"]
    proposal_status: Literal["REVIEW_REQUIRED"]
    publication_status: Literal["NOT_PUBLISHED"]

    target_agent: str

    grounded_facts: list[str]
    inferences: list[str]
    unresolved: list[str]

    governance: GovernancePack
    value_realization: ValueRealizationPack
    model_routing: ModelRoutingPack
    portfolio: PortfolioPack
    delegation_boundary: DelegationBoundary
    action: ActionDecision

    grounding_status: Literal["PENDING_VALIDATION", "PASS"]
    grounding_issues: list[str] = Field(default_factory=list)


class EvidenceImpactResult(BaseModel):
    impact: Literal["MATERIAL", "NO_MATERIAL_CHANGE", "UNRESOLVED"]
    treatment: Literal["REASSESS", "SUSPEND", "HUMAN_REVIEW", "NO_CHANGE"]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    rationale: str
    affected_sections: list[str]
    before_summary: list[str]
    after_summary: list[str]
    material_change: bool
