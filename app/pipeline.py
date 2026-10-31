"""主链路：文档入库 -> 主张抽取 -> 同一事件识别/合并 -> 命题判定 -> 版本演化 -> 通知。

事件状态优先级：disputed > closed > corrected > denied > confirmed > developing > emerging
整体状态取"证据权重最高的命题"的判定；证据冲突（disputed）始终优先暴露。
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import compliance, db, llm


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _evidence_weight(doc: dict, claim_type: str) -> int:
    authority = llm.SOURCE_AUTHORITY.get(doc.get("source", ""), 2)
    return int(authority * 20 * llm.TYPE_WEIGHT.get(claim_type, 0.5))


def _event_topic(event: dict) -> str:
    conclusion = event.get("conclusion") or "{}"
    try:
        props = json.loads(conclusion).get("propositions") or {}
        prop_text = " ".join(p["proposition"] for p in props.values())
    except Exception:
        prop_text = ""
    return f"{event.get('title', '')} {prop_text}"


def _bucket_of(text: str) -> str:
    for bucket, kws in llm.KEYWORD_BUCKETS.items():
        if any(k.lower() in (text or "").lower() for k in kws):
            return bucket
    return ""


def _score_candidates(events: list[dict], claim: dict, doc_text: str = "") -> list[tuple[dict, int]]:
    """规则打分：主体交集(每个+2) + 命题/标题一致(+3) + 关键词桶一致(+1)。"""
    # 关键词桶综合主张文本与整篇文档文本（如"开盘跌停"本身无关键词，但文档通篇在讲借壳）
    claim_bucket = _bucket_of(
        claim.get("core_proposition", "") + claim.get("summary", "") + claim.get("quote", "") + doc_text
    )
    claim_prop = llm._normalize_proposition(claim.get("core_proposition") or "")
    scored = []
    for e in events:
        shared = set(llm._as_list(e.get("entity_tags"))) & set(llm._as_list(claim.get("subjects")))
        # 命题或标题完全一致且足够具体（≥3 字、非通用词）→ 强合并信号
        topic = _event_topic(e)
        prop_match = 0
        if len(claim_prop) >= 3 and claim_prop not in {"公告", "新闻", "报道", "快讯", "研报"}:
            try:
                props = json.loads(e.get("conclusion") or "{}").get("propositions") or {}
            except Exception:
                props = {}
            if claim_prop in props or claim_prop == llm._normalize_proposition(e.get("title") or ""):
                prop_match = 3
        bucket_match = 1 if claim_bucket and claim_bucket == _bucket_of(topic) else 0
        score = 2 * len(shared) + prop_match + bucket_match
        if score > 0:
            scored.append((e, score))
    scored.sort(key=lambda x: -x[1])
    return scored


def _find_or_create_event(claim: dict, doc_text: str = "") -> tuple[dict, bool]:
    """返回 (event, is_new)。"""
    events = db.list_events()
    scored = _score_candidates(events, claim, doc_text)
    if not scored:
        return _create_event(claim), True
    if scored[0][1] >= 3:  # 主体交集 + 关键词桶，明确合并
        return scored[0][0], False
    # 灰区：桶一致但主体无交集等 -> LLM 判定（缓存/降级）
    candidates = [
        {"event_id": e["event_id"], "title": e["title"], "entity_tags": e["entity_tags"],
         "topic": _event_topic(e)}
        for e, _ in scored[:3]
    ]
    decision = llm.judge_merge(candidates, claim)
    if decision.get("decision") == "merge" and decision.get("event_id"):
        for e, _ in scored:
            if e["event_id"] == decision["event_id"]:
                return e, False
    return _create_event(claim), True


def _create_event(claim: dict) -> dict:
    prop = llm._normalize_proposition(claim.get("core_proposition") or "未命名事件")
    title = re.sub(r"市场传闻", "", claim.get("core_proposition") or "未命名事件").strip("（）() ")
    event = {
        "event_id": "evt_" + uuid.uuid4().hex[:10],
        "title": title or "未命名事件",
        "entity_tags": json.dumps(llm._as_list(claim.get("subjects")), ensure_ascii=False),
        "status": "emerging",
        "conclusion": "{}",
        "event_time": claim.get("event_time") or "",
        "latest_disclose": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "primary_doc_id": "",
    }
    db.insert_event(event)
    db.insert_version({"event_id": event["event_id"], "change_type": "create",
                       "trigger_doc_id": "", "summary": f"事件创建：{title}",
                       "conclusion_before": "", "conclusion_after": "{}", "created_at": now_iso()})
    db.insert_notification({"event_id": event["event_id"], "notif_type": "new_event",
                            "title": f"新事件：{title}",
                            "message": f"系统识别到新事件「{title}」（主体：{'、'.join(claim.get('subjects', [])) or '未识别'}）",
                            "created_at": now_iso()})
    return event


def _verdict_for(claim: dict, doc: dict) -> str | None:
    """权威来源的事实主张才能翻转判定。返回 None 表示不改变判定。"""
    if claim.get("claim_type") != "fact":
        return None
    if doc.get("source") not in ("公司公告", "官方公告", "互动易", "权威媒体"):
        return None
    text = (claim.get("summary") or "") + (claim.get("quote") or "")
    # "不存在应披露而未披露的重大事项"是公告标准表述（事实声明），不是对传闻的否认
    if "应披露" in text:
        return "confirmed"
    if any(k in text for k in llm.DENY_KW):
        return "denied"
    if any(k in text for k in llm.CORRECT_KW):
        return "corrected"
    if any(k in text for k in llm.CLOSE_KW):
        return "closed"
    return "confirmed"


def _propositions_of(event: dict) -> dict:
    try:
        return json.loads(event.get("conclusion") or "{}").get("propositions") or {}
    except Exception:
        return {}


TYPE_CN = {"fact": "事实", "opinion": "观点", "speculation": "推测", "rumor": "传闻"}


def _apply_claim(event: dict, claim: dict, doc: dict) -> list[dict]:
    """把主张并入事件：命题归并、判定更新、版本与通知。返回该主张触发的通知。"""
    notifs = []
    conclusion_before = event.get("conclusion") or "{}"
    prop_key = llm._normalize_proposition(claim.get("core_proposition") or "")
    if not prop_key:
        prop_key = llm._normalize_proposition(doc.get("title") or "")
    conclusion = json.loads(conclusion_before)
    if not isinstance(conclusion, dict):
        conclusion = {}
    propositions = conclusion.get("propositions")
    if not isinstance(propositions, dict):
        propositions = {}
        conclusion["propositions"] = propositions  # 必须挂回 conclusion，避免孤儿字典
    verdict = _verdict_for(claim, doc)
    prop = propositions.get(prop_key)
    flipped = False

    if prop is None:
        prop = {
            "proposition": claim.get("core_proposition") or prop_key,
            "verdict": "unverified",
            "by_source": "", "by_time": "", "by_weight": 0, "by_doc": "",
            "claims": 0, "authoritative_weight": 0,
        }
        propositions[prop_key] = prop
        db.insert_version({"event_id": event["event_id"], "change_type": "proposition_added",
                           "trigger_doc_id": doc["doc_id"],
                           "summary": f"新增命题：「{prop['proposition']}」",
                           "conclusion_before": conclusion_before,
                           "conclusion_after": json.dumps(conclusion, ensure_ascii=False),
                           "created_at": now_iso()})

    prop["claims"] += 1
    weight = _evidence_weight(doc, claim.get("claim_type"))

    VERDICT_RANK = {"unverified": 0, "confirmed": 1, "denied": 1, "corrected": 2, "closed": 3}

    if verdict:
        prop["authoritative_weight"] += weight
        if verdict != prop["verdict"] and verdict in ("confirmed", "denied", "corrected", "closed"):
            new_rank, cur_rank = VERDICT_RANK[verdict], VERDICT_RANK.get(prop["verdict"], 0)
            if new_rank > cur_rank:
                flipped = True
                prop["verdict"] = verdict  # 更高优先级判定取代旧判定（如完结取代证实）
                change, notif_type = verdict, "state_change" if verdict != "closed" else "closed"
                msg = f"命题「{prop['proposition']}」判定更新为【{compliance.VERDICT_CN.get(verdict, verdict)}】"
            elif new_rank == cur_rank:
                flipped = True
                prop["verdict"] = "disputed"  # 同优先级判定冲突（证实 vs 否认）
                change, notif_type = "disputed", "conflict"
                msg = f"新证据与既有判定冲突，命题「{prop['proposition']}」进入证据冲突状态"
            else:
                flipped = False  # 低优先级判定不覆盖高优先级，仅作证据更新
            if flipped:
                prop["by_source"] = f"{doc.get('publisher', '')} · {doc.get('source', '')}"
                prop["by_time"] = doc.get("disclose_time") or ""
                prop["by_weight"] = weight
                prop["by_doc"] = doc["doc_id"]
                event["conclusion"] = json.dumps(conclusion, ensure_ascii=False)
                db.insert_version({"event_id": event["event_id"], "change_type": change,
                                   "trigger_doc_id": doc["doc_id"], "summary": msg,
                                   "conclusion_before": conclusion_before,
                                   "conclusion_after": event["conclusion"], "created_at": now_iso()})
                notifs.append({"event_id": event["event_id"], "notif_type": notif_type,
                               "title": f"结论更新：{event['title']}",
                               "message": f"{msg}。依据：{doc['title']}（{doc.get('disclose_time') or '时间未知'}）",
                               "created_at": now_iso()})

    if not flipped:
        # 每条证据都留痕：非判定类主张与"同判定加强"的事实均记入版本
        change = "update" if claim.get("claim_type") in ("fact", "opinion", "speculation") else "rumor"
        event["conclusion"] = json.dumps(conclusion, ensure_ascii=False)
        db.insert_version({"event_id": event["event_id"], "change_type": change,
                           "trigger_doc_id": doc["doc_id"],
                           "summary": f"新增{TYPE_CN.get(claim.get('claim_type'), '证据')}证据：{claim.get('summary','')[:50]}",
                           "conclusion_before": conclusion_before,
                           "conclusion_after": event["conclusion"], "created_at": now_iso()})
        if claim.get("claim_type") == "fact":
            notifs.append({"event_id": event["event_id"], "notif_type": "update",
                           "title": f"事件更新：{event['title']}",
                           "message": f"新增事实证据：{claim.get('summary','')[:80]}（来源：{doc['title']}）",
                           "created_at": now_iso()})
    return notifs


def _recompute_status(event: dict) -> None:
    conclusion = json.loads(event.get("conclusion") or "{}")
    if not isinstance(conclusion, dict):
        conclusion = {}
    propositions = conclusion.get("propositions")
    if not isinstance(propositions, dict):
        propositions = {}
        conclusion["propositions"] = propositions
    if not propositions:
        return
    if any(p["verdict"] == "disputed" for p in propositions.values()):
        status = "disputed"
    else:
        dominant = max(propositions.values(), key=lambda p: p.get("authoritative_weight", 0))
        status = dominant.get("verdict") or "unverified"
        if status == "unverified":
            has_fact = db.count_event_claims(event["event_id"]).get("fact", 0) > 0
            status = "developing" if has_fact else "emerging"
    event["status"] = status  # 先定状态，statement 引用它
    conclusion["evidence_counts"] = db.count_event_claims(event["event_id"])
    conclusion["statement"] = compliance.build_statement(
        event, list(propositions.values()), conclusion["evidence_counts"]
    )
    event["conclusion"] = json.dumps(conclusion, ensure_ascii=False)


def ingest_document(meta: dict, body: str, doc_id: str | None = None, consolidate: bool = True) -> dict:
    """主链路入口。meta: title/source/publisher/url/publish_time/event_time。

    doc_id 不传时随机生成（在线入库）；bootstrap 回放时传稳定文件名，便于缓存复用与审计。
    consolidate=True 时在入库末尾运行事件合并后处理（批量回放可关闭，最后统一跑一次）。
    """
    doc_id = doc_id or "doc_" + uuid.uuid4().hex[:10]
    doc = {
        "doc_id": doc_id,
        "title": meta.get("title", ""),
        "source": meta.get("source", "一般媒体"),
        "publisher": meta.get("publisher", ""),
        "url": meta.get("url", ""),
        "event_time": meta.get("event_time", ""),
        "disclose_time": meta.get("publish_time", "") or meta.get("disclose_time", ""),
        "crawl_time": now_iso(),
        "body": body,
        "body_complete": int(meta.get("body_complete", 1)),
        "fetch_status": "ok",
        "proposition_hint": meta.get("proposition_hint", ""),
    }
    db.insert_document(doc)

    claims, method = llm.extract_claims(doc)
    if method == "rule":
        doc["fetch_status"] = "degraded"  # LLM 接口异常 -> 规则降级，如实标记
        db.insert_document(doc)

    changes = []
    all_notifs = []
    for i, c in enumerate(claims):
        claim = {
            "claim_id": f"{doc_id}_c{i}",
            "doc_id": doc_id,
            "event_id": "",
            "claim_type": c.get("type") or "fact",
            "summary": c.get("summary", ""),
            "quote": c.get("quote", ""),
            "subjects": json.dumps(llm.canonicalize_subjects(c.get("subjects")), ensure_ascii=False),
            "core_proposition": c.get("core_proposition", ""),
            "event_time": c.get("event_time", ""),
            "evidence_weight": 0,
            "extraction_method": method,
        }
        doc_text = f"{doc.get('title', '')} {doc.get('body', '')}"
        event, is_new = _find_or_create_event(claim, doc_text)
        claim["event_id"] = event["event_id"]
        if not is_new:
            # 事件主体随合并累积，提升后续匹配能力
            merged_tags = list(dict.fromkeys(
                llm._as_list(event.get("entity_tags")) + llm._as_list(claim.get("subjects"))))
            event["entity_tags"] = json.dumps(merged_tags, ensure_ascii=False)
        claim["evidence_weight"] = _evidence_weight(doc, claim["claim_type"])
        db.insert_claim(claim)
        notifs = _apply_claim(event, claim, doc)
        all_notifs.extend(notifs)
        changes.append({"event_id": event["event_id"], "proposition": c.get("core_proposition", ""),
                        "claim_type": claim["claim_type"], "notifications": len(notifs)})

        # 更新时间戳与状态
        _recompute_status(event)
        disclose = doc.get("disclose_time") or ""
        candidates = [t for t in (event.get("latest_disclose") or "", disclose) if t]
        event["latest_disclose"] = max(candidates) if candidates else ""
        event["event_time"] = claim.get("event_time") or event.get("event_time") or ""
        event["primary_doc_id"] = event.get("primary_doc_id") or doc_id
        event["updated_at"] = now_iso()
        db.insert_event(event)

    for n in all_notifs:
        db.insert_notification(n)
    merged = consolidate_events() if consolidate else []
    return {"doc_id": doc_id, "fetch_status": doc["fetch_status"], "extraction_method": method,
            "claims": len(claims), "changes": changes, "notifications": all_notifs,
            "merged_events": merged}


def _affinity_pairs(events: list[dict]) -> list[tuple[dict, dict]]:
    """事件对亲和候选：共享主体，或话题关键词桶一致。"""
    pairs = []
    for i in range(len(events)):
        for j in range(i + 1, len(events)):
            a, b = events[i], events[j]
            shared = set(llm._as_list(a.get("entity_tags"))) & set(llm._as_list(b.get("entity_tags")))
            bucket_a = _bucket_of(_event_topic(a))
            bucket_b = _bucket_of(_event_topic(b))
            if shared or (bucket_a and bucket_a == bucket_b):
                pairs.append((a, b))
    return pairs


def _merge_events(keep: dict, absorb: dict, reason: str = "") -> dict:
    """把 absorb 并入 keep：主张/版本迁移、命题并集、标签合并、版本留痕、通知。"""
    conclusion = json.loads(keep.get("conclusion") or "{}")
    if not isinstance(conclusion, dict):
        conclusion = {}
    props = conclusion.get("propositions")
    if not isinstance(props, dict):
        props = {}
        conclusion["propositions"] = props
    other = json.loads(absorb.get("conclusion") or "{}")
    for key, p in (other.get("propositions") or {}).items():
        if key in props:
            props[key]["claims"] = props[key].get("claims", 0) + p.get("claims", 0)
            props[key]["authoritative_weight"] = props[key].get("authoritative_weight", 0) + p.get("authoritative_weight", 0)
            rank = {"unverified": 0, "confirmed": 1, "denied": 1, "corrected": 2, "closed": 3}
            if rank.get(p.get("verdict"), 0) > rank.get(props[key].get("verdict"), 0):
                props[key].update({k: p.get(k) for k in ("verdict", "by_source", "by_time", "by_weight", "by_doc")})
        else:
            props[key] = p

    merged_tags = list(dict.fromkeys(
        llm._as_list(keep.get("entity_tags")) + llm._as_list(absorb.get("entity_tags"))))
    keep["entity_tags"] = json.dumps(merged_tags, ensure_ascii=False)
    time_cands = [t for t in (keep.get("event_time") or "", absorb.get("event_time") or "") if t]
    keep["event_time"] = min(time_cands) if time_cands else ""
    cands = [t for t in (keep.get("latest_disclose") or "", absorb.get("latest_disclose") or "") if t]
    keep["latest_disclose"] = max(cands) if cands else ""

    db.move_claims(absorb["event_id"], keep["event_id"])
    db.move_versions(absorb["event_id"], keep["event_id"])
    _recompute_status(keep)
    db.insert_version({"event_id": keep["event_id"], "change_type": "merge",
                       "trigger_doc_id": "", "summary": f"事件合并：吸收「{absorb.get('title', '')}」（{reason or '同一事件线'}）",
                       "conclusion_before": "", "conclusion_after": keep["conclusion"],
                       "created_at": now_iso()})
    db.insert_notification({"event_id": keep["event_id"], "notif_type": "merge_event",
                            "title": f"事件合并：{keep['title']}",
                            "message": f"系统识别「{absorb.get('title', '')}」与本事件同属一条事件线（{reason or '同一事件线'}），已合并证据与版本历史。",
                            "created_at": now_iso()})
    db.delete_event(absorb["event_id"])
    db.insert_event(keep)
    return keep


def consolidate_events(max_rounds: int = 3) -> list[dict]:
    """事件合并后处理：对亲和事件对做两两判定并合并（主链路"合并"环节的显式实现）。"""
    merged_log = []
    for _ in range(max_rounds):
        events = db.list_events()
        pairs = _affinity_pairs(events)
        if not pairs:
            break
        changed = False
        for a, b in pairs:
            if db.get_event(a["event_id"]) is None or db.get_event(b["event_id"]) is None:
                continue
            a, b = db.get_event(a["event_id"]), db.get_event(b["event_id"])
            decision = llm.judge_event_pair(
                {**a, "topic": _event_topic(a)}, {**b, "topic": _event_topic(b)})
            if decision.get("decision") == "merge":
                _merge_events(a, b, decision.get("reason", ""))
                merged_log.append({"from": b["event_id"], "to": a["event_id"],
                                   "reason": decision.get("reason", "")})
                changed = True
        if not changed:
            break
    return merged_log


def apply_expiry(event: dict) -> bool:
    """过期处理：非完结事件超过 60 天无更新 -> 标记过期并通知（仅一次）。"""
    if event.get("status") == "closed":
        return False
    try:
        updated = datetime.fromisoformat(event.get("updated_at") or now_iso())
    except ValueError:
        return False
    if datetime.now(timezone.utc).astimezone() - updated < timedelta(days=60):
        return False
    if db.has_notification_of_type(event["event_id"], "expiry"):
        return False
    event["status"] = "expired"
    conclusion = json.loads(event.get("conclusion") or "{}")
    conclusion["statement"] = compliance.build_statement(
        event, (conclusion.get("propositions") or {}).values(), conclusion.get("evidence_counts")
    )
    event["conclusion"] = json.dumps(conclusion, ensure_ascii=False)
    db.insert_event(event)
    db.insert_version({"event_id": event["event_id"], "change_type": "expired",
                       "trigger_doc_id": "", "summary": "超过 60 天无更新，事件标记为已过期",
                       "conclusion_before": "", "conclusion_after": event["conclusion"],
                       "created_at": now_iso()})
    db.insert_notification({"event_id": event["event_id"], "notif_type": "expiry",
                            "title": f"事件过期：{event['title']}",
                            "message": f"「{event['title']}」已超过 60 天无新证据，标记为已过期。结论仍保留证据时间线，可随时回溯。",
                            "created_at": now_iso()})
    return True
