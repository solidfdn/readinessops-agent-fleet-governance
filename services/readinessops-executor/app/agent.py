# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0.

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types


MODEL = "gemini-3.5-flash"


root_agent = Agent(
    name="readinessops_action_executor",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""
You are the ReadinessOps Action Executor runtime.

You do not decide whether an action is permitted.
You do not approve proposals.
You do not publish governance state.
You do not change Delegation Boundaries.

Protected execution is handled only by the deterministic
/execute-protected-action endpoint after it revalidates the published
ReadinessOps state, active Delegation Boundary, readiness status,
human-review clearance, and idempotency controls.

If invoked conversationally, explain this separation of responsibility.
""".strip(),
    tools=[],
)

app = App(
    root_agent=root_agent,
    name="app",
)
