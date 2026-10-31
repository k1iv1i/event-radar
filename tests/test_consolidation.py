"""事件合并后处理测试：同事件线合并、无关事件保留。"""
import json

from conftest import make_doc

from app import db, pipeline


def _insert_event(eid, title, tags, prop_name, verdict):
    now = pipeline.now_iso()
    db.insert_event({
        "event_id": eid, "title": title,
        "entity_tags": json.dumps(tags, ensure_ascii=False),
        "status": verdict, "conclusion": json.dumps({"propositions": {
            prop_name: {"proposition": prop_name, "verdict": verdict, "by_source": "测试",
                        "by_time": "2025-01-01", "by_weight": 100, "by_doc": "d",
                        "claims": 1, "authoritative_weight": 100}}}, ensure_ascii=False),
        "event_time": "", "latest_disclose": "", "created_at": now, "updated_at": now,
        "primary_doc_id": "",
    })


def _insert_claim(cid, eid, prop):
    db.insert_claim({"claim_id": cid, "doc_id": "d_" + cid, "event_id": eid,
                     "claim_type": "fact", "summary": prop, "quote": prop,
                     "subjects": json.dumps(["左江科技"], ensure_ascii=False),
                     "core_proposition": prop, "event_time": "",
                     "evidence_weight": 100, "extraction_method": "rule"})


def test_consolidation_merges_same_entity_events():
    _insert_event("evt_testa", "*ST左江被证监会立案", ["左江科技"], "左江被立案", "confirmed")
    _insert_event("evt_testb", "*ST左江终止上市", ["左江科技"], "左江终止上市", "closed")
    _insert_claim("c1", "evt_testa", "左江被立案")
    _insert_claim("c2", "evt_testb", "左江终止上市")

    log = pipeline.consolidate_events()
    assert len(log) == 1  # 共享主体 -> 合并
    events = db.list_events()
    assert len(events) == 1
    assert db.count_event_claims("evt_testa")["fact"] == 2  # 主张已迁移
    versions = db.list_versions("evt_testa")
    assert any(v["change_type"] == "merge" for v in versions)  # 合并留痕
    assert db.has_notification_of_type("evt_testa", "merge_event")  # 合并通知
    concl = json.loads(events[0]["conclusion"])
    assert len(concl["propositions"]) == 2  # 命题并集


def test_consolidation_keeps_unrelated_events():
    make_doc("成飞传闻", "据传成飞集团将借壳成飞集成上市。", publish_time="2020-05-01",
             proposition_hint="成飞集团借壳成飞集成")
    make_doc("左江立案", "左江科技收到证监会立案告知书。", source="公司公告", publisher="*ST左江",
             publish_time="2023-12-01", proposition_hint="左江科技被立案")
    log = pipeline.consolidate_events()
    assert log == []  # 无共享主体、话题不同 -> 保留两个事件
    assert len(db.list_events()) == 2


def test_consolidation_merges_then_is_idempotent():
    _insert_event("evt_testa", "事件A", ["左江科技"], "命题A", "confirmed")
    _insert_event("evt_testb", "事件B", ["左江科技"], "命题B", "confirmed")
    log1 = pipeline.consolidate_events()
    assert len(log1) == 1
    assert pipeline.consolidate_events() == []  # 再跑无新增合并
    assert len(db.list_events()) == 1
