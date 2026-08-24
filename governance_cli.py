import argparse
import json
import subprocess

from app.governance import (
    approve_proposal,
    get_current_state,
    get_proposal,
    publish_proposal,
    reject_proposal,
    review_and_edit_proposal,
)


def actor():
    return subprocess.check_output(
        ["gcloud", "config", "get-value", "account"],
        text=True,
    ).strip()


parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="command", required=True)

p = sub.add_parser("show")
p.add_argument("proposal_id")

p = sub.add_parser("review")
p.add_argument("proposal_id")
p.add_argument("--comment", required=True)
p.add_argument("--add-unresolved")

p = sub.add_parser("approve")
p.add_argument("proposal_id")
p.add_argument("--comment", required=True)

p = sub.add_parser("reject")
p.add_argument("proposal_id")
p.add_argument("--reason", required=True)

p = sub.add_parser("publish")
p.add_argument("proposal_id")
p.add_argument("--comment", required=True)

p = sub.add_parser("current")
p.add_argument("target_agent")

args = parser.parse_args()

if args.command == "show":
    result = get_proposal(args.proposal_id)
elif args.command == "review":
    result = review_and_edit_proposal(
        args.proposal_id, actor(), args.comment, args.add_unresolved
    )
elif args.command == "approve":
    result = approve_proposal(args.proposal_id, actor(), args.comment)
elif args.command == "reject":
    result = reject_proposal(args.proposal_id, actor(), args.reason)
elif args.command == "publish":
    result = publish_proposal(args.proposal_id, actor(), args.comment)
elif args.command == "current":
    result = get_current_state(args.target_agent)

print(json.dumps(result, default=str, indent=2))
