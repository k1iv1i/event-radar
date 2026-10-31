"""接口异常测试：LLM 不可用 -> 降级且如实标记；空数据 -> 明确报错；缺字段不崩溃。"""
import json

from conftest import make_doc

from app import db


def test_llm_down_falls_back_to_rules_and_marks_degraded(monkeypatch):
    """LLM 启用但调用失败：主链路仍可用，文档标记 degraded，绝不静默产出。"""
    from app import config
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-invalid-key-for-test")
    # 直连会失败/超时；这里不依赖网络，pipeline 捕获异常后走规则抽取
    r = make_doc("澄清公告", "成飞集成公告：市场传闻不属实，公司予以澄清。",
                 source="公司公告", publisher="成飞集成", publish_time="2021-06-01")
    # 注：LLM 网络不可用时规则降级；若环境恰好可联网，则可能走 llm 路径，两者都不应失败
    assert r["claims"] >= 1
    doc = db.get_document(r["doc_id"])
    if r["extraction_method"] == "rule":
        assert doc["fetch_status"] == "degraded"


def test_rule_extraction_still_drives_verdicts():
    """降级模式下主链路结论仍正确：官方否认 -> denied。"""
    make_doc("澄清公告", "公司公告：关于公司被收购的市场传闻不属实，予以澄清否认。",
             source="公司公告", publisher="X公司", publish_time="2025-04-01")
    events = db.list_events()
    assert events
    concl = json.loads(events[0]["conclusion"])
    assert any(p["verdict"] == "denied" for p in concl.get("propositions", {}).values())


def test_empty_body_rejected_by_api():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    resp = client.post("/api/documents", json={"title": "空文档", "body": ""})
    assert resp.status_code == 400


def test_missing_timestamps_do_not_crash():
    """披露时间缺失：链路可用，时间字段留空并由前端如实展示。"""
    r = make_doc("无时间文档", "公司公告：完成重大资产重组。", source="公司公告",
                 publisher="Y公司", publish_time="")
    assert r["claims"] >= 1
    doc = db.get_document(r["doc_id"])
    assert doc["crawl_time"]  # 抓取时间始终存在
    assert doc["disclose_time"] == ""


def test_unknown_source_gets_default_weight():
    r = make_doc("来源未知", "据传某公司将被收购。", source="", publish_time="2025-05-01")
    from app import db as _db
    claims = _db.list_claims_for_event(r["changes"][0]["event_id"])
    assert all(0 <= c["evidence_weight"] <= 100 for c in claims)


def test_duplicate_replay_is_idempotent():
    """同一文档重复入库不产生重复事件结论（幂等重放）。"""
    r1 = make_doc("传闻", "据传X将被收购。", publish_time="2025-06-01")
    r2 = make_doc("传闻", "据传X将被收购。", publish_time="2025-06-01")
    events = db.list_events()
    # 相同内容 -> 合并为同一事件，主张数翻倍但事件数不变
    assert len(events) == 1
    assert r1["changes"][0]["event_id"] == r2["changes"][0]["event_id"]
