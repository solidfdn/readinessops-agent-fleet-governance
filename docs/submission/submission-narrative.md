# ReadinessOps — Submission Narrative

## Core Category

Fortified Enterprise Fleet

## One-Sentence Description

ReadinessOps converts changing evidence into governed agent decisions,
human-published delegation boundaries, and identity-isolated execution
permissions on Google Cloud.

## The Problem

Organizations can deploy AI agents faster than they can maintain the evidence,
human decisions, and execution permissions that justify what those agents are
allowed to do.

When policies, controls, operating conditions, or evidence change, an agent
may continue operating under an outdated safety basis. Existing monitoring
can report that something changed, but it does not necessarily connect that
change to:

- reassessment,
- human review,
- publication of a revised delegation boundary,
- actual execution permissions,
- and auditable recovery.

## The User

ReadinessOps is designed for the people who must safely operate enterprise AI
without being model engineers:

- CCoE leads,
- AI governance owners,
- operational risk reviewers,
- service owners,
- and human approvers.

## What ReadinessOps Does

ReadinessOps maintains a governed lifecycle for enterprise agents:

1. New evidence arrives asynchronously.
2. Model Armor checks the evidence before LLM processing.
3. A private evidence worker creates a governed revision.
4. Specialized agents reassess evidence impact, governance, value,
   routing, and portfolio implications.
5. Material safety drift suspends the affected agent.
6. AI output remains REVIEW_REQUIRED and NOT_PUBLISHED.
7. A human reviews and edits the proposed delegation boundary.
8. Approval alone does not change the official current state.
9. Explicit publication creates a new active delegation boundary.
10. The agent can be reactivated only against that published boundary.
11. A deterministic gate evaluates protected action requests.
12. A separate execution identity performs only permitted actions.
13. One trace ID connects the evidence event to human decisions and execution.

## Why This Requires an Agent Fleet

The workflow separates distinct responsibilities:

- Evidence Impact Agent:
  determines how new evidence changes the governed safety basis.

- Governance Agent:
  proposes gaps, risks, controls, and required actions.

- Value and Portfolio Agent:
  proposes value metrics, proceed / hold / stop decisions, and conditions
  before expansion.

- Model Routing Agent:
  separates delegable work from work requiring human judgment.

- Governance Orchestrator:
  consolidates the four decision packs but cannot approve, publish, or execute.

- Action Executor:
  performs deterministic revalidation and protected execution under a separate
  Agent Identity.

This separation prevents a single model response from becoming an operational
decision or execution permission.

## Verified Golden Path

The following path has been executed in the Google Cloud hackathon environment:

Evidence upload
→ Cloud Storage OBJECT_FINALIZE
→ Pub/Sub
→ authenticated private Cloud Run worker
→ Model Armor
→ governed revision
→ multi-agent reassessment
→ READY to SUSPENDED
→ four decision packs
→ deterministic grounding validation
→ REVIEW_REQUIRED proposal
→ human review
→ approval without publication
→ explicit publication
→ Delegation Boundary v2
→ READY reactivation
→ deterministic action gate
→ Analysis Identity denied
→ Executor Identity permitted
→ Agent Gateway
→ protected Pub/Sub action
→ end-to-end trace

## Verified Safety Behaviors

- AI cannot approve or publish its own proposal.
- Approval does not change the official current state.
- Explicit publication is required to activate a delegation boundary.
- Material evidence drift can automatically suspend an agent.
- Prompt-injection evidence is blocked before revision, LLM, or proposal creation.
- Analysis / Governance Identity cannot execute protected actions.
- A separate Executor Identity can execute only actions permitted by the active
  published boundary.
- Actions outside the active boundary are denied even when the agent is READY.
- Duplicate permitted action requests are ignored.
- A failed multi-agent reassessment can retry using the same governed revision.
- A common trace ID connects evidence, revision, proposal, human decision,
  publication, action request, execution claim, and protected execution.

## Google Cloud Technologies

- Gemini 3.5 Flash on Vertex AI
- Google Agent Development Kit
- Vertex AI Agent Runtime
- Agent Identity
- Agent Gateway
- Agent Registry and A2A Agent Cards
- Model Armor
- Cloud Run
- Pub/Sub
- Cloud Storage
- Firestore
- Cloud Build
- Artifact Registry
- Cloud Logging and telemetry

## Data Sources

The current hackathon demonstration uses synthetic text evidence.

No production customer data or personally identifiable information is used.

Governed records include:

- source evidence,
- Cloud Storage event metadata,
- revisions,
- proposals,
- publications,
- delegation boundaries,
- human decisions,
- action requests,
- execution claims,
- Model Armor results,
- and audit events.

## Architectural Principle

AI proposes.
People decide.
Publication defines the official state.
Published boundaries control execution.
Separate identities enforce the difference between analysis and action.

## What Is New in This Hackathon Implementation

The ReadinessOps product concept and an earlier Snowflake-oriented prototype
predated this hackathon.

This Google Cloud implementation was created during the All Things Agentic
Hackathon submission period.

New work includes:

- Google ADK multi-agent orchestration,
- Gemini 3.5 Flash reassessment,
- Vertex AI Agent Runtime,
- Agent Identity,
- Agent Gateway enforcement,
- Model Armor pre-LLM blocking,
- Cloud Storage and Pub/Sub event ingestion,
- authenticated private Cloud Run evidence processing,
- Firestore governance records,
- evidence-driven automatic suspension,
- human-published delegation boundaries,
- identity-isolated protected execution,
- deterministic grounding validation,
- idempotent action handling,
- retry recovery using the same governed revision,
- and end-to-end governance traceability.

## Current Limitations

- The judge demonstration uses synthetic evidence.
- The current evidence worker focuses on text evidence.
- A general-purpose PDF parsing pipeline is not part of the submitted build.
- Production enterprise connectors are intentionally deferred.
- Firestore is used as the official governance record for this implementation.
- The judge-facing English UI is being finalized as a presentation layer over
  the verified backend workflow.

## Primary Submission Claims

ReadinessOps does not claim that AI can safely govern itself.

It demonstrates that:

1. AI-generated assessments can remain proposals.
2. human publication can define a machine-readable delegation boundary,
3. actual execution permissions can be isolated from analytical capability,
4. changing evidence can automatically suspend unsafe operation,
5. and the full decision-to-execution path can remain auditable.
