import json
import os
from datetime import datetime

import google.auth
import requests
from flask import Flask, jsonify, request, send_file
from google.auth.transport.requests import Request
from google.cloud import firestore

app = Flask(__name__)

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "readinessops-agent-governance")
PROJECT_NUMBER = os.getenv("READINESSOPS_PROJECT_NUMBER", "649947189508")
REGION = os.getenv("READINESSOPS_REGION", "asia-northeast1")
GOVERNANCE_RUNTIME_ID = os.getenv(
    "READINESSOPS_GOVERNANCE_RUNTIME_ID",
    "2493591216726212608",
)
DEFAULT_AGENT_ID = os.getenv("READINESSOPS_AGENT_ID", "case-triage-agent")


def _db():
    return firestore.Client(project=PROJECT_ID)


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, tuple):
        return [_clean(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _doc(db, collection, document_id):
    if not document_id:
        return {}
    snap = db.collection(collection).document(document_id).get()
    return snap.to_dict() if snap.exists else {}


def _recent(db, collection, limit=50):
    try:
        docs = (
            db.collection(collection)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception:
        return []


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "readinessops-judge-ui",
        "project": PROJECT_ID,
    }


@app.get("/architecture.png")
def architecture():
    return send_file(
        "/app/static/readinessops-architecture.png",
        mimetype="image/png",
    )


@app.get("/api/state")
def api_state():
    agent_id = request.args.get("agent_id", DEFAULT_AGENT_ID)
    db = _db()

    agent = _doc(db, "agents", agent_id)
    current = _doc(db, "current_states", agent_id)

    revision_id = current.get("revision_id") or agent.get("current_revision_id")
    proposal_id = current.get("proposal_id")
    boundary_id = (
        current.get("delegation_boundary_id")
        or agent.get("active_delegation_boundary_id")
    )

    revision = _doc(db, "revisions", revision_id)
    proposal = _doc(db, "proposals", proposal_id)
    boundary = _doc(db, "delegation_boundaries", boundary_id)

    trace_id = (
        current.get("trace_id")
        or revision.get("trace_id")
        or proposal.get("trace_id")
    )

    events = _recent(db, "event_receipts", 40)
    actions = _recent(db, "action_requests", 40)
    audits = _recent(db, "audit_events", 120)

    trace_event = next(
        (x for x in events if x.get("event_id") == trace_id),
        {},
    )
    blocked_event = next(
        (x for x in events if x.get("status") == "BLOCKED"),
        {},
    )

    trace_actions = [x for x in actions if x.get("trace_id") == trace_id]
    executed_action = next(
        (
            x for x in trace_actions
            if x.get("execution_status") == "EXECUTED"
        ),
        {},
    )
    denied_action = next(
        (x for x in trace_actions if x.get("status") == "DENIED"),
        {},
    )

    trace_audits = [x for x in audits if x.get("trace_id") == trace_id]
    trace_audits.reverse()

    payload = proposal.get("proposal") or {}
    decision_packs = {
        "governance": payload.get("governance"),
        "value_realization": payload.get("value_realization"),
        "model_routing": payload.get("model_routing"),
        "portfolio": payload.get("portfolio"),
    }

    return jsonify(
        _clean(
            {
                "agent_id": agent_id,
                "trace_id": trace_id,
                "agent": agent,
                "current": current,
                "revision": revision,
                "proposal": proposal,
                "boundary": boundary,
                "trace_event": trace_event,
                "blocked_event": blocked_event,
                "executed_action": executed_action,
                "denied_action": denied_action,
                "decision_packs": decision_packs,
                "timeline": trace_audits,
            }
        )
    )


@app.post("/api/identity-a-probe")
def identity_a_probe():
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())

    url = (
        f"https://{REGION}-aiplatform.googleapis.com/"
        f"reasoningEngines/v1/projects/{PROJECT_NUMBER}/locations/{REGION}/"
        f"reasoningEngines/{GOVERNANCE_RUNTIME_ID}/api/protected-action-probe"
    )

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        },
        json={},
        timeout=45,
    )

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:1000]}

    return jsonify(
        {
            "http_status": response.status_code,
            "runtime_id": GOVERNANCE_RUNTIME_ID,
            "result": body,
        }
    ), 200 if response.ok else 502


HTML = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReadinessOps Judge Console</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#0f172a;background:#f8fafc}
*{box-sizing:border-box}
body{margin:0}
header{background:#0f172a;color:#fff;padding:24px 30px}
header h1{margin:0;font-size:25px}
header p{margin:7px 0 0;color:#cbd5e1}
main{max-width:1500px;margin:auto;padding:22px}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:18px}
button{border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;background:#1d4ed8;color:#fff}
button.secondary{background:#334155}
.chip{display:inline-block;padding:5px 9px;border-radius:999px;background:#e2e8f0;font-size:12px;font-weight:800}
.chip.good{background:#dcfce7;color:#166534}.chip.bad{background:#fee2e2;color:#991b1b}.chip.warn{background:#ffedd5;color:#9a3412}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;box-shadow:0 1px 2px rgba(15,23,42,.05)}
.card h2{font-size:17px;margin:0 0 14px}
.kv{display:grid;grid-template-columns:190px 1fr;gap:8px 14px;font-size:14px}
.k{color:#64748b}.v{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-word}
.section{margin-top:16px}
.timeline{max-height:330px;overflow:auto}
.event{border-left:3px solid #cbd5e1;padding:8px 10px;margin:8px 0;background:#f8fafc}
.event b{font-size:13px}.event small{display:block;color:#64748b;margin-top:3px}
.arch{width:100%;border:1px solid #e2e8f0;border-radius:10px;background:#fff}
pre{white-space:pre-wrap;word-break:break-word;background:#0f172a;color:#e2e8f0;border-radius:9px;padding:12px;font-size:12px;max-height:260px;overflow:auto}
@media(max-width:900px){.grid{grid-template-columns:1fr}.kv{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>ReadinessOps — Judge Console</h1>
  <p>Evidence-driven governance and identity-isolated execution on Google Cloud.</p>
</header>
<main>
  <div class="toolbar">
    <button onclick="loadState()">Refresh governed state</button>
    <button class="secondary" onclick="runProbe()">Run Analysis Identity A probe</button>
    <span id="loadStatus" class="chip">Loading…</span>
  </div>

  <div class="grid">
    <section class="card">
      <h2>1 · Fleet State</h2>
      <div class="kv">
        <div class="k">Agent</div><div class="v" id="agentId">—</div>
        <div class="k">Readiness</div><div id="readiness">—</div>
        <div class="k">Current Publication</div><div class="v" id="publication">—</div>
        <div class="k">Boundary Version</div><div class="v" id="boundaryVersion">—</div>
        <div class="k">Trace ID</div><div class="v" id="traceId">—</div>
      </div>
    </section>

    <section class="card">
      <h2>2 · Evidence & Reassessment</h2>
      <div class="kv">
        <div class="k">Evidence Event</div><div class="v" id="eventId">—</div>
        <div class="k">Event Status</div><div id="eventStatus">—</div>
        <div class="k">Material Change</div><div class="v" id="materialChange">—</div>
        <div class="k">Model Armor</div><div id="armorStatus">—</div>
        <div class="k">Decision Packs</div><div class="v" id="packs">—</div>
      </div>
    </section>

    <section class="card">
      <h2>3 · Human Decision</h2>
      <div class="kv">
        <div class="k">Proposal</div><div class="v" id="proposalId">—</div>
        <div class="k">Proposal Status</div><div id="proposalStatus">—</div>
        <div class="k">Approved By</div><div class="v" id="approvedBy">—</div>
        <div class="k">Published By</div><div class="v" id="publishedBy">—</div>
        <div class="k">Permitted Actions</div><div class="v" id="permittedActions">—</div>
        <div class="k">Prohibited Actions</div><div class="v" id="prohibitedActions">—</div>
      </div>
    </section>

    <section class="card">
      <h2>4 · Governed Execution</h2>
      <div class="kv">
        <div class="k">Executed Action</div><div class="v" id="executedAction">—</div>
        <div class="k">Execution Status</div><div id="executionStatus">—</div>
        <div class="k">Pub/Sub Message ID</div><div class="v" id="messageId">—</div>
        <div class="k">Denied Action</div><div class="v" id="deniedAction">—</div>
      </div>
      <div class="section">
        <b>Live Analysis Identity A proof</b>
        <pre id="probeResult">Click “Run Analysis Identity A probe”.</pre>
      </div>
    </section>
  </div>

  <section class="card section">
    <h2>Governance Timeline</h2>
    <div id="timeline" class="timeline"></div>
  </section>

  <section class="card section">
    <h2>Architecture</h2>
    <img class="arch" src="/architecture.png" alt="ReadinessOps architecture">
  </section>
</main>

<script>
const el=id=>document.getElementById(id);
const txt=(id,v)=>el(id).textContent=(v??"—");
const chip=(id,v,kind="")=>el(id).innerHTML=`<span class="chip ${kind}">${v??"—"}</span>`;

async function loadState(){
  chip("loadStatus","Refreshing…","");
  const r=await fetch("/api/state");
  const d=await r.json();
  txt("agentId",d.agent_id);
  const ready=d.agent?.readiness_status;
  chip("readiness",ready,ready==="READY"?"good":ready==="SUSPENDED"?"bad":"warn");
  txt("publication",d.current?.publication_id);
  txt("boundaryVersion",d.boundary?.version);
  txt("traceId",d.trace_id);
  txt("eventId",d.trace_event?.event_id);
  chip("eventStatus",d.trace_event?.status,d.trace_event?.status==="COMPLETED"?"good":"warn");
  txt("materialChange",d.revision?.material_change);
  const blocked=d.blocked_event?.status==="BLOCKED";
  chip("armorStatus",blocked?"BLOCKED malicious evidence":"No blocked event found",blocked?"bad":"");
  const packs=Object.entries(d.decision_packs||{}).filter(([,v])=>v!=null).map(([k])=>k);
  txt("packs",packs.join(" · "));
  txt("proposalId",d.current?.proposal_id);
  chip("proposalStatus",d.proposal?.proposal_status,d.proposal?.proposal_status==="PUBLISHED"?"good":"warn");
  txt("approvedBy",d.proposal?.approved_by);
  txt("publishedBy",d.proposal?.published_by);
  txt("permittedActions",(d.boundary?.permitted_actions||[]).join(", "));
  txt("prohibitedActions",(d.boundary?.prohibited_actions||[]).join(", "));
  txt("executedAction",d.executed_action?.action_name);
  chip("executionStatus",d.executed_action?.execution_status,d.executed_action?.execution_status==="EXECUTED"?"good":"");
  txt("messageId",d.executed_action?.message_id);
  txt("deniedAction",d.denied_action?.action_name);
  const timeline=el("timeline");
  timeline.innerHTML="";
  for(const a of (d.timeline||[])){
    const div=document.createElement("div");
    div.className="event";
    div.innerHTML=`<b>${a.event_type||"AUDIT_EVENT"}</b><small>${a.created_at||""}</small>`;
    timeline.appendChild(div);
  }
  chip("loadStatus","Live state loaded","good");
}

async function runProbe(){
  el("probeResult").textContent="Running protected-action probe from Analysis Identity A…";
  const r=await fetch("/api/identity-a-probe",{method:"POST"});
  const d=await r.json();
  el("probeResult").textContent=JSON.stringify(d,null,2);
}

loadState().catch(e=>{
  chip("loadStatus","Load failed","bad");
  console.error(e);
});
</script>
</body>
</html>'''


@app.get("/")
def index():
    return HTML
