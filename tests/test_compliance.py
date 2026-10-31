"""合规边界测试：无买卖建议/涨跌预测；事实与传闻严格区分；结论可追溯。"""
import json

from conftest import make_doc

from app import compliance, db


def test_conclusion_never_contains_advice():
    make_doc("研报", "分析师强烈推荐买入，目标价翻倍。", source="研报", publish_time="2025-01-01")
    make_doc("公告", "公司公告：业绩预告修正，净利润下修50%。", source="公司公告",
             publisher="X公司", publish_time="2025-01-02")
    for e in db.list_events():
        concl = json.loads(e["conclusion"])
        statement = concl.get("statement", "")
        assert not compliance.contains_advice(statement), f"结论含建议性表述：{statement}"
        assert "不构成任何投资建议" in statement


def test_sanitize_strips_advice_phrases():
    dirty = "目标价50元，建议买入，强烈推荐，必涨。"
    clean = compliance.sanitize(dirty)
    assert "建议买入" not in clean and "目标价" not in clean and "必涨" not in clean


def test_claim_types_strictly_separated_and_traceable():
    """事实/观点/推测/传闻逐条可溯：每条主张绑定来源文档与原文引用。"""
    from app import db as _db
    make_doc("传闻稿", "据传公司将被收购。", source="社交媒体", publish_time="2025-02-01",
             proposition_hint="公司被收购（市场传闻）")
    make_doc("澄清公告", "公司公告：上述传闻不属实。", source="公司公告", publisher="X公司",
             publish_time="2025-02-02", proposition_hint="公司被收购（市场传闻）")
    event = db.list_events()[0]
    claims = _db.list_claims_for_event(event["event_id"])
    assert len(claims) >= 2
    types = {c["claim_type"] for c in claims}
    assert "rumor" in types and "fact" in types  # 传闻与事实分开
    for c in claims:
        doc = _db.get_document(c["doc_id"])
        assert doc["title"] and doc["disclose_time"]  # 来源与披露时间可溯
        assert c["quote"]  # 每条主张都有原文引用


def test_api_disclaimer_present():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    resp = client.get("/api/events")
    assert resp.status_code == 200
    assert "不构成任何投资建议" in resp.json()["disclaimer"]
    resp2 = client.get("/api/stats")
    assert "不构成任何投资建议" in resp2.json()["disclaimer"]
