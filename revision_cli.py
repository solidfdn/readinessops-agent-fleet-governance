import argparse
import json
import subprocess

from app.revision import create_draft_revision

parser = argparse.ArgumentParser()
parser.add_argument("target_agent")
parser.add_argument("--evidence", required=True)
parser.add_argument("--source", required=True)
args = parser.parse_args()

actor = subprocess.check_output(
    ["gcloud", "config", "get-value", "account"],
    text=True,
).strip()

result = create_draft_revision(
    target_agent=args.target_agent,
    evidence_text=args.evidence,
    source_label=args.source,
    actor=actor,
)

print(json.dumps(result, default=str, indent=2))
