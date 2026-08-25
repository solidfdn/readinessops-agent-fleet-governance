# P0 Product Requirement — Governance Workspace

## Status

REQUIRED / NOT DEFERRABLE WITHOUT EXPLICIT HUMAN APPROVAL

## Requirement

ReadinessOps must provide an authenticated user-facing operating workspace
that lets a governance operator perform the governed lifecycle, not merely
observe its result.

The workspace must support:

1. adding text evidence;
2. observing asynchronous reassessment and safety suspension;
3. reviewing the four Decision Packs and proposed Delegation Boundary;
4. recording Human Review;
5. editing the Delegation Boundary;
6. approving the proposal;
7. confirming that approval does not change Current;
8. explicitly publishing the reviewed boundary;
9. reactivating READY only against the published boundary;
10. requesting a protected action;
11. executing a permitted request through the isolated Executor identity;
12. observing a denied out-of-boundary request;
13. verifying that Analysis Identity cannot perform protected execution.

## Governance Rule

This requirement may not be removed, deferred, or replaced by a read-only
dashboard, video, or documentation without an explicit human decision.

Any proposed scope change must identify:

- the requirement being changed;
- the reason;
- the product and judging impact;
- the proposed treatment;
- and the human approver.

## Separation of Responsibilities

- Governance Workspace: used by operators to perform the lifecycle.
- Judge Console: read-only proof that the governed state and execution controls
  are operating in Google Cloud.
