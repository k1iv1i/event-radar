"""主链路测试：识别/合并/命题归并/判定翻转/版本/通知/完结/过期。"""
import json

from conftest import make_doc

from app import db, pipeline


def test_rumor_to_denial_flips_conclusion_and_notifies():
    make_doc("市场传闻：成飞集团将借壳成飞集成", "据传成飞集团将借壳成飞集成上市，市场消息称注入方案已上报。",
             source="一般媒体", publish_time="2020-05-01", proposition_hint="成飞集团借壳成飞集成（市场传闻）")
    events = db.list_events()
    assert len(events) == 1
    assert events[0]["status"] == "emerging"  # 仅有传闻 -> 传闻阶段

    make_doc("成飞集成关于市场传闻的澄清公告", "本公司对市场传闻予以澄清：成飞集团借壳本公司上市的消息不属实，公司未筹划相关事项。",
             source="公司公告", publisher="成飞集成", publish_time="2020-05-02", proposition_hint="成飞集团借壳成飞集成（市场传闻）")
    event = db.get_event(events[0]["event_id"])
    assert event["status"] == "denied"  # 官方否认 -> 结论翻转

    concl = json.loads(event["conclusion"])
    props = concl["propositions"]
    # 传闻与澄清归并为同一命题（命题文本经归一化去括号）
    assert len(props) == 1
    p = list(props.values())[0]
    assert p["verdict"] == "denied"
    assert "成飞集成" in p["by_source"] and p["by_doc"]

    # 通知与版本留痕
    notifs = db.list_notifications()
    assert any(n["notif_type"] == "state_change" for n in notifs)
    versions = db.list_versions(event["event_id"])
    assert any(v["change_type"] == "denied" for v in versions)
    assert any(v["change_type"] == "rumor" for v in versions)


def test_conflicting_evidence_marks_disputed():
    make_doc("传闻：X公司将被收购", "据传X公司将被某巨头收购。", publish_time="2025-02-01",
             proposition_hint="X公司被收购")
    make_doc("X公司澄清公告", "X公司公告：公司被收购的传闻不属实。", source="公司公告",
             publisher="X公司", publish_time="2025-02-02", proposition_hint="X公司被收购")
    make_doc("X公司公告", "X公司公告：公司确与收购方签署了协议，收购属实。", source="公司公告",
             publisher="X公司", publish_time="2025-02-03", proposition_hint="X公司被收购")
    event = db.list_events()[0]
    assert event["status"] == "disputed"  # 否认后又证实 -> 证据冲突
    concl = json.loads(event["conclusion"])
    p = list(concl["propositions"].values())[0]
    assert p["verdict"] == "disputed"
    assert any(n["notif_type"] == "conflict" for n in db.list_notifications())


def test_same_event_merges_by_entity_and_topic():
    r1 = make_doc("停牌公告", "中航电测公告筹划发行股份购买成飞集团100%股权。", source="公司公告",
                  publisher="中航电测", publish_time="2023-01-12", proposition_hint="成飞集团借壳中航电测")
    r2 = make_doc("预案公告", "中航电测披露重组预案，拟向航空工业集团发行股份购买成飞集团100%股权。",
                  source="公司公告", publisher="中航电测", publish_time="2023-02-02",
                  proposition_hint="成飞集团借壳中航电测")
    events = db.list_events()
    assert len(events) == 1  # 主体交集（成飞集团/中航电测）+ 话题一致 -> 合并为同一事件
    event = events[0]
    assert db.count_event_claims(event["event_id"])["fact"] == 2
    assert event["status"] == "confirmed"


def test_closure_supersedes_and_versions_accumulate():
    make_doc("立案公告", "*ST左江公告：收到证监会立案告知书。", source="公司公告",
             publisher="*ST左江", publish_time="2023-12-01", proposition_hint="*ST左江被立案调查")
    make_doc("退市公告", "*ST左江公告：公司股票被终止上市，将于2024年7月摘牌。", source="公司公告",
             publisher="*ST左江", publish_time="2024-07-15", proposition_hint="*ST左江被立案调查")
    event = db.list_events()[0]
    assert event["status"] == "closed"
    versions = db.list_versions(event["event_id"])
    assert any(v["change_type"] == "closed" for v in versions)
    # 每个版本都有 before/after 快照
    assert all(v["conclusion_after"] for v in versions)


def test_four_timestamps_recorded():
    make_doc("事件文档", "事实内容", publish_time="2025-03-01", event_time="2025-02-28")
    doc = db.list_documents()[0]
    for field in ("disclose_time", "crawl_time", "event_time"):
        assert doc[field], f"缺少时间字段 {field}"
    event = db.list_events()[0]
    assert event["event_time"]  # 事件发生时间
    assert event["updated_at"]  # 更新时间


def test_expiry_marks_event_once():
    import datetime as dt
    make_doc("传闻文档", "据传某事件将发生。", publish_time="2024-01-01")
    event = db.list_events()[0]
    # 回拨更新时间到 70 天前，模拟长期无更新
    event["updated_at"] = (dt.datetime.now(dt.timezone.utc).astimezone() - dt.timedelta(days=70)).isoformat()
    db.insert_event(event)
    assert pipeline.apply_expiry(event) is True
    event = db.get_event(event["event_id"])
    assert event["status"] == "expired"
    assert db.has_notification_of_type(event["event_id"], "expiry")
    assert pipeline.apply_expiry(event) is False  # 只通知一次


def test_different_entities_never_merge():
    """回归：主体/话题均不交集的文档必须各自成事件（曾因 JSON 字符串字符集交集 bug 全部合并）。"""
    make_doc("成飞传闻", "据传成飞集团将借壳成飞集成上市。", publish_time="2020-05-01")
    make_doc("左江立案", "左江科技收到证监会立案告知书。", source="公司公告", publisher="*ST左江",
             publish_time="2023-12-01")
    make_doc("超导澄清", "法尔胜公告：公司不涉及室温超导业务。", source="公司公告", publisher="法尔胜",
             publish_time="2023-08-03")
    events = db.list_events()
    assert len(events) == 3  # 三个独立事件，不得互相合并
    tags = " ".join(str(e["entity_tags"]) for e in events)
    assert "成飞" in tags and "左江" in tags and "法尔胜" in tags


def test_offline_cache_replay_is_deterministic(tmp_path, monkeypatch):
    """缓存回放：同样的原始文档两次引导应得到相同事件数与状态。"""
    import json
    from app import config

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "mini.json").write_text(json.dumps({
        "title": "停牌公告", "source": "公司公告", "publisher": "中航电测",
        "url": "https://example.com/a", "publish_time": "2023-01-12", "event_time": "2023-01-11",
        "body": "中航电测公告：筹划发行股份购买成飞集团100%股权，公司股票自1月12日起停牌。",
        "body_complete": True, "event": "demo",
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "RAW_DIR", raw_dir)

    from scripts.bootstrap_demo import main
    assert main() == 0
    n_events = len(db.list_events())
    assert n_events == 1
    assert db.list_events()[0]["status"] == "confirmed"
    assert main() == 0  # 重放不崩溃，事件数不变
    assert len(db.list_events()) == n_events
