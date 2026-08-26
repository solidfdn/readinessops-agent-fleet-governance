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
        "access": "judge-console",
    }


@app.get("/architecture")
def architecture():
    return send_file(
        "/app/static/readinessops-architecture.html",
        mimetype="text/html",
    )


@app.get("/api/state")
def api_state():
    agent_id = DEFAULT_AGENT_ID
    db = _db()

    agent = _doc(db, "agents", agent_id)

    target_agent = (
        agent.get("target_agent")
        or agent.get("name")
        or agent_id
    )

    target_slug = (
        str(target_agent)
        .strip()
        .lower()
        .replace(" ", "-")
    )

    current = _doc(
        db,
        "current_states",
        target_slug,
    )

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
    security_event = trace_event or {}

    if (
        security_event.get("status") != "BLOCKED"
        and security_event.get("security_status") != "BLOCKED"
    ):
        security_event = trace_event or {}

    trace_actions = [x for x in actions if x.get("trace_id") == trace_id]
    executed_action = next(
        (x for x in trace_actions if x.get("execution_status") == "EXECUTED"),
        {},
    )
    denied_action = next(
        (x for x in trace_actions if x.get("status") == "DENIED"),
        {},
    )

    trace_audits = [x for x in audits if x.get("trace_id") == trace_id]
    trace_audits.reverse()

    payload = proposal.get("proposal") or {}
    evidence_impact = revision.get("evidence_impact") or {}
    armor = security_event.get("model_armor") or {}

    pack_names = [
        key
        for key in ("governance", "value_realization", "model_routing", "portfolio")
        if payload.get(key) is not None
    ]

    approval_recorded = any(
        x.get("event_type") == "PROPOSAL_APPROVED" for x in trace_audits
    )
    publication_recorded = any(
        x.get("event_type") == "PROPOSAL_PUBLISHED" for x in trace_audits
    )

    def short_ref(value):
        if value is None:
            return None
        text = str(value)
        if len(text) <= 18:
            return text
        return f"{text[:10]}…{text[-6:]}"

    denial_reasons = denied_action.get("gate_reasons") or []
    if not denial_reasons and denied_action.get("reason"):
        denial_reasons = [denied_action["reason"]]

    public_timeline = [
        {
            "event_type": item.get("event_type"),
            "created_at": item.get("created_at"),
            "actor_type": item.get("actor_type"),
        }
        for item in trace_audits
    ]

    return jsonify(
        _clean(
            {
                "agent": {
                    "name": agent.get("name") or agent.get("target_agent") or "Case Triage Agent",
                    "readiness_status": agent.get("readiness_status"),
                },
                "governance": {
                    "trace_ref": short_ref(trace_id),
                    "event_status": trace_event.get("status"),
                    "material_change": revision.get("material_change"),
                    "impact": evidence_impact.get("impact"),
                    "treatment": evidence_impact.get("treatment"),
                    "decision_packs": pack_names,
                    "proposal_status": proposal.get("proposal_status"),
                    "publication_status": proposal.get("publication_status"),
                    "boundary_version": boundary.get("version"),
                    "permitted_actions": boundary.get("permitted_actions") or [],
                    "prohibited_actions": boundary.get("prohibited_actions") or [],
                    "approval_recorded": approval_recorded,
                    "publication_recorded": publication_recorded,
                    "approval_separate_from_publication": approval_recorded and publication_recorded,
                },
                "security": {
                    "status": (
                        security_event.get("security_status")
                        or security_event.get("status")
                    ),
                    "filter_match_state": armor.get("filter_match_state"),
                    "confidence_level": armor.get("confidence_level"),
                },
                "execution": {
                    "executed_action": executed_action.get("action_name"),
                    "execution_status": executed_action.get("execution_status"),
                    "message_ref": short_ref(executed_action.get("message_id")),
                    "denied_action": denied_action.get("action_name"),
                    "denial_reason": "; ".join(denial_reasons[:2]),
                },
                "timeline": public_timeline,
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
        body = {}

    return jsonify(
        {
            "control": "Analysis Identity A protected egress",
            "outcome": body.get("outcome") or "UNRESOLVED",
            "destination": body.get("destination"),
            "protected_http_status": body.get("http_status"),
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
.chip{display:inline-block;padding:5px 9px;border-radius:999px;background:#e2e8f0;font-size:12px;font-weight:600}
.chip.good{background:#EAF3FF;color:#0B1F3A;border:1px solid #BFDBFE}.chip.bad{background:#F8FAFC;color:#0B1F3A;border:1px solid #C7D5E8}.chip.warn{background:#F8FBFF;color:#64748B;border:1px solid #D8E2F0}
.lifecycle{margin:0 0 18px;padding:13px 16px;border:1px solid #bfdbfe;border-radius:12px;background:#eff6ff;color:#1e3a8a;font-weight:600;font-size:14px}
.lifecycle strong{color:#172554}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;box-shadow:0 1px 2px rgba(15,23,42,.05)}
.card h2{font-size:17px;margin:0 0 14px}
.kv{display:grid;grid-template-columns:190px 1fr;gap:8px 14px;font-size:14px}
.k{color:#64748b}.v{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-word}
.section{margin-top:16px}
.note{font-size:12px;color:#64748b;line-height:1.4}
.timeline{max-height:330px;overflow:auto}
.event{border-left:3px solid #cbd5e1;padding:8px 10px;margin:8px 0;background:#f8fafc}
.event b{font-size:13px}.event small{display:block;color:#64748b;margin-top:3px}
.arch{width:100%;border:1px solid #e2e8f0;border-radius:10px;background:#fff}
pre{
  white-space:pre-wrap;
  word-break:break-word;
  background:#FFFFFF;
  color:#0B1F3A;
  border:1px solid #D8E2F0;
  border-radius:8px;
  padding:10px 12px;
  font-size:11px;
  font-weight:400;
  line-height:1.55;
  max-height:260px;
  overflow:auto;
  box-shadow:none;
}
.probe-proof{
  white-space:pre-wrap;
  word-break:break-word;
  margin:10px 0 0;
  padding:12px 14px;
  min-height:58px;
  background:#F8FBFF;
  color:#0B1F3A;
  border:1px solid #D8E2F0;
  border-left:3px solid #246BFD;
  border-radius:10px;
  box-shadow:none;
  font-size:13px;
  font-weight:400;
  line-height:1.55;
}
.probe-proof.denied{
  background:#F8FBFF;
  color:#0B1F3A;
  border:1px solid #D8E2F0;
  border-left:3px solid #246BFD;
}
@media(max-width:900px){.grid{grid-template-columns:1fr}.kv{grid-template-columns:1fr}}

.solifan-screen-mark{
  position:absolute;
  top:18px;
  right:22px;
  width:64px;
  height:auto;
  z-index:20;
  pointer-events:none;
  user-select:none;
}

@media (max-width:900px){
  .solifan-screen-mark{
    top:14px;
    right:16px;
    width:52px;
  }
}

</style>

<style id="solifan-readinessops-ui-v2">
:root{
  --solifan-navy:#0B1F3A;
  --solifan-blue:#246BFD;
  --solifan-blue-soft:#EAF3FF;
  --solifan-border:#D8E2F0;
  --solifan-muted:#64748B;
  --solifan-surface:#FFFFFF;
  --solifan-page:#F8FAFC;
  --solifan-success-bg:#EAF3FF;
  --solifan-success:#0B1F3A;
  --solifan-warning-bg:#F8FBFF;
  --solifan-danger-bg:#F8FAFC;
  --solifan-danger:#0B1F3A;
}

/* Quiet, SOLIFAN-like surface language */
body{
  background:var(--solifan-page) !important;
  color:var(--solifan-navy) !important;
}
header,
.topbar,
.app-header,
.page-header{
  background:var(--solifan-surface) !important;
  color:var(--solifan-navy) !important;
  border-bottom:1px solid var(--solifan-border) !important;
  box-shadow:none !important;
}
header h1, header h2, header h3,
.topbar h1, .app-header h1, .page-header h1{
  color:var(--solifan-navy) !important;
}
header p, header small,
.topbar p, .app-header p, .page-header p{
  color:var(--solifan-muted) !important;
}

.card,
.panel,
section.card,
[class~="card"]{
  background:var(--solifan-surface) !important;
  border-color:var(--solifan-border) !important;
  box-shadow:none !important;
}

h1,h2,h3,h4{
  color:var(--solifan-navy);
}

button,
.btn{
  border-radius:8px !important;
  box-shadow:none !important;
  min-height:36px;
  font-weight:600;
}

/* Separate control from outcome. */
.actions,
.button-row,
.button-group{
  gap:10px !important;
  margin-top:14px !important;
  margin-bottom:16px !important;
  align-items:center !important;
}

/* Result is a status card, not a console. */
.result,
.result-box,
.status-output,
.proof-result{
  margin-top:14px !important;
  padding:13px 15px !important;
  min-height:58px !important;
  background:#F8FBFF !important;
  color:var(--solifan-navy) !important;
  border:1px solid var(--solifan-border) !important;
  border-radius:10px !important;
  box-shadow:none !important;
  line-height:1.45 !important;
}

/* Works even when the legacy result container has no stable class. */
*:has(> .solifan-result-title){
  margin-top:14px !important;
  padding:13px 15px !important;
  min-height:58px !important;
  background:#F8FBFF !important;
  color:var(--solifan-navy) !important;
  border:1px solid var(--solifan-border) !important;
  border-radius:10px !important;
  box-shadow:none !important;
  line-height:1.45 !important;
}

.solifan-result-title{
  display:block;
  font-size:14px;
  font-weight:600;
  color:var(--solifan-navy);
}
.solifan-result-caption{
  display:block;
  margin-top:4px;
  font-size:12px;
  font-weight:400;
  color:var(--solifan-muted);
}

.result details,
.result-box details,
*:has(> .solifan-result-title) details{
  margin-top:8px !important;
}

details{
  border-color:var(--solifan-border) !important;
  box-shadow:none !important;
}

input, textarea, select{
  border-color:#C7D5E8 !important;
  border-radius:8px !important;
  box-shadow:none !important;
}
input:focus, textarea:focus, select:focus{
  outline:none !important;
  border-color:var(--solifan-blue) !important;
  box-shadow:0 0 0 2px rgba(36,107,253,.10) !important;
}

/* Status is communicated by wording and hierarchy,
   not traffic-light colors. */
.chip.good,
.status-success,
.pill.success{
  background:var(--solifan-success-bg) !important;
  color:var(--solifan-success) !important;
  border:1px solid #BFDBFE !important;
}

.chip.warn{
  background:var(--solifan-warning-bg) !important;
  color:var(--solifan-muted) !important;
  border:1px solid var(--solifan-border) !important;
}

.chip.bad,
.status-danger,
.pill.danger,
.danger{
  background:var(--solifan-danger-bg) !important;
  color:var(--solifan-danger) !important;
  border:1px solid #C7D5E8 !important;
}

/* Never let an empty/probe placeholder dominate the card. */
.solifan-proof-idle{
  max-width:680px;
}

.proof-label{
  color:var(--solifan-muted);
  font-size:12px;
  font-weight:600;
  letter-spacing:.01em;
}

.probe-proof .solifan-result-title{
  font-weight:600;
}

.probe-proof .solifan-result-caption{
  font-weight:400;
}
</style>

</head>
<body>
<img class="solifan-screen-mark" src="/static/solifan-judge-mark.png" alt="" aria-hidden="true">
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

  <div class="lifecycle">
    <strong>Governed recovery path:</strong>
    Evidence change → Automatic SUSPEND → Human review → Explicit Publish → READY in reduced scope
  </div>

  <div class="grid">
    <section class="card">
      <h2>1 · Fleet State</h2>
      <div class="kv">
        <div class="k">Agent</div><div class="v" id="agentId">—</div>
        <div class="k">Readiness</div><div id="readiness">—</div>
        <div class="k">Current State</div><div class="v" id="publication">—</div>
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
        <div class="k">Security Gate Test</div><div id="armorStatus">—</div>
        <div class="k">Decision Packs</div><div class="v" id="packs">—</div>
      </div>
    </section>

    <section class="card">
      <h2>3 · Human Decision</h2>
      <div class="kv">
        <div class="k">Proposal</div><div class="v" id="proposalId">—</div>
        <div class="k">Proposal Status</div><div id="proposalStatus">—</div>
        <div class="k">Approval</div><div class="v" id="approvedBy">—</div>
        <div class="k">Publication</div><div class="v" id="publishedBy">—</div>
        <div class="k">Permitted Actions</div><div class="v" id="permittedActions">—</div>
        <div class="k">Prohibited Actions</div><div class="v" id="prohibitedActions">—</div>
      </div>
    </section>

    <section class="card">
      <h2>4 · Governed Execution</h2>
      <div class="kv">
        <div class="k">Executed Action</div><div class="v" id="executedAction">—</div>
        <div class="k">Execution Status</div><div id="executionStatus">—</div>
        <div class="k">Pub/Sub Message</div><div class="v" id="messageId">—</div>
        <div class="k">Denied Automated Action</div><div class="v" id="deniedAction">—</div>
        <div class="k"></div><div class="note">Outside automated boundary — manual handling required</div>
      </div>
      <div class="section">
        <div class="proof-label">Live Analysis Identity A proof</div>
        <div id="probeResult" class="probe-proof"><span class="solifan-result-title">Analysis Identity isolation</span><span class="solifan-result-caption">Run the probe to verify that Analysis Runtime A cannot execute protected actions.</span></div>
      </div>
    </section>
  </div>

  <section class="card section">
    <h2>Governance Timeline</h2>
    <div id="timeline" class="timeline"></div>
  </section>

  <section class="card section">
    <h2>Architecture</h2>
    <iframe src="/architecture" title="ReadinessOps architecture" style="display:block;width:100%;aspect-ratio:16/9;border:0;border-radius:12px;background:#FFFFFF;overflow:hidden;"></iframe>
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

  txt("agentId",d.agent?.name);
  const ready=d.agent?.readiness_status;
  chip("readiness",ready,ready==="READY"?"good":ready==="SUSPENDED"?"bad":"warn");

  txt("publication",d.governance?.publication_status==="PUBLISHED"
    ?"Published boundary active":"Not published");
  txt("boundaryVersion",d.governance?.boundary_version);
  txt("traceId",d.governance?.trace_ref);

  txt("eventId",d.governance?.trace_ref);
  chip("eventStatus",d.governance?.event_status,
    d.governance?.event_status==="COMPLETED"?"good":"warn");
  txt("materialChange",
    d.governance?.material_change
      ? `${d.governance?.impact||"MATERIAL"} · ${d.governance?.treatment||"REASSESS"}`
      : "No");

  const blocked=d.security?.filter_match_state==="MATCH_FOUND";
  chip("armorStatus",
    blocked
      ? `BLOCKED · ${d.security?.confidence_level||"detected"}`
      : "No malicious input detected",
    blocked?"bad":"good");

  txt("packs",(d.governance?.decision_packs||[]).join(" · "));
  txt("proposalId","Evidence-driven reassessment");
  chip("proposalStatus",d.governance?.proposal_status,
    d.governance?.proposal_status==="PUBLISHED"?"good":"warn");

  txt("approvedBy",
    d.governance?.approval_recorded
      ?"Recorded separately from publication":"Not recorded");
  txt("publishedBy",
    d.governance?.publication_recorded
      ?"Explicit publication recorded":"Not published");

  txt("permittedActions",(d.governance?.permitted_actions||[]).join(", "));
  txt("prohibitedActions",(d.governance?.prohibited_actions||[]).join(", "));

  txt("executedAction",d.execution?.executed_action);
  chip("executionStatus",d.execution?.execution_status,
    d.execution?.execution_status==="EXECUTED"?"good":"");
  txt("messageId",d.execution?.message_ref);
  txt("deniedAction",d.execution?.denied_action);

  const timeline=el("timeline");
  timeline.innerHTML="";
  for(const a of (d.timeline||[])){
    const div=document.createElement("div");
    div.className="event";
    div.innerHTML=`<b>${a.event_type||"AUDIT_EVENT"}</b><small>${a.created_at||""} · ${a.actor_type||"SYSTEM"}</small>`;
    timeline.appendChild(div);
  }
  chip("loadStatus","Live governed state loaded","good");
}

async function runProbe(){
  const box=el("probeResult");
  box.className="probe-proof";
  box.textContent="Running protected-action probe from Analysis Identity A…";

  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),15000);

  try{
    const r=await fetch(
      "/api/identity-a-probe",
      {
        method:"POST",
        signal:controller.signal
      }
    );

    const contentType=r.headers.get("content-type")||"";

    if(!r.ok){
      throw new Error(`Probe request failed · HTTP ${r.status}`);
    }

    if(!contentType.includes("application/json")){
      throw new Error("Probe returned an unexpected response.");
    }

    const d=await r.json();

    const outcome=d.outcome??"—";
    const status=d.protected_http_status??"—";
    const explanation=outcome==="DENIED"
      ?"Analysis Identity cannot execute protected actions."
      :"Unexpected probe result — review before demo.";

    box.className=outcome==="DENIED"
      ?"probe-proof denied"
      :"probe-proof";

    box.textContent=`${outcome} · HTTP ${status}
${explanation}`;
  }catch(error){
    box.className="probe-proof";

    const message=error.name==="AbortError"
      ?"Probe timed out. Please retry."
      :error.message;

    box.textContent=`Probe unavailable
${message}`;
  }finally{
    clearTimeout(timer);
  }
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
