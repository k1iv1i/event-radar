"""FastAPI 入口：事件/时间线查询、实时文档入库（主链路在线操作）、通知、健康检查。"""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import compliance, db, pipeline

app = FastAPI(title="事件雷达 · 投资事件情报与证据时间线", version="0.1.0")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

db.init_db()


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "llm_enabled": __import__("app.config", fromlist=["LLM_ENABLED"]).LLM_ENABLED}


def _event_payload(event: dict) -> dict:
    conclusion = json.loads(event.get("conclusion") or "{}")
    return {**event, "conclusion": conclusion, "status_cn": compliance.STATUS_CN.get(event["status"], event["status"])}


@app.get("/api/events")
def list_events():
    out = []
    for e in db.list_events():
        pipeline.apply_expiry(e)
        out.append(_event_payload(db.get_event(e["event_id"])))
    return {"events": out, "disclaimer": compliance.DISCLAIMER}


@app.get("/api/events/{event_id}")
def get_event_detail(event_id: str):
    event = db.get_event(event_id)
    if not event:
        raise HTTPException(404, "事件不存在")
    expired = pipeline.apply_expiry(event)
    event = db.get_event(event_id)
    claims = []
    for c in db.list_claims_for_event(event_id):
        doc = db.get_document(c["doc_id"]) or {}
        claims.append({**c, "subjects": json.loads(c.get("subjects") or "[]"),
                       "doc": {k: doc.get(k) for k in ("doc_id", "title", "source", "publisher", "url",
                                                       "disclose_time", "crawl_time", "event_time",
                                                       "fetch_status")}})
    documents = db.list_documents()
    docs = [d for d in documents if any(c["doc_id"] == d["doc_id"] for c in claims)]
    return {
        "event": _event_payload(event),
        "versions": db.list_versions(event_id),
        "claims": claims,
        "documents": docs,
        "expiry_applied": expired,
    }


class IngestRequest(BaseModel):
    title: str
    body: str
    source: str = "一般媒体"
    publisher: str = ""
    url: str = ""
    publish_time: str = ""
    event_time: str = ""
    body_complete: int = 1


@app.post("/api/documents")
def ingest(req: IngestRequest):
    """在线操作主链路：提交一篇新文档，实时完成抽取/合并/判定/通知。"""
    if not req.body.strip():
        raise HTTPException(400, "正文为空")
    result = pipeline.ingest_document(req.model_dump(), req.body)
    return {**result, "disclaimer": compliance.DISCLAIMER}


@app.get("/api/notifications")
def list_notifications():
    return {"notifications": db.list_notifications()}


@app.post("/api/notifications/{notif_id}/read")
def read_notification(notif_id: int):
    db.mark_notification_read(notif_id)
    return {"ok": True}


@app.get("/api/stats")
def stats():
    events = db.list_events()
    claims_all = []
    docs = db.list_documents()
    for e in events:
        claims_all.extend(db.list_claims_for_event(e["event_id"]))
    return {
        "events": len(events),
        "documents": len(docs),
        "claims": len(claims_all),
        "claim_types": {t: sum(1 for c in claims_all if c["claim_type"] == t)
                        for t in ("fact", "opinion", "speculation", "rumor")},
        "extraction_methods": {m: sum(1 for c in claims_all if c["extraction_method"] == m)
                               for m in ("llm", "cache", "rule")},
        "sources": {d["source"]: sum(1 for x in docs if x["source"] == d["source"])
                    for d in docs},
        "status_counts": {e["status"]: sum(1 for x in events if x["status"] == e["status"])
                          for e in events},
        "disclaimer": compliance.DISCLAIMER,
    }
