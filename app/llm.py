"""LLM 抽取与事件合并判定（DeepSeek，OpenAI 兼容），带缓存与规则降级。

三级降级链：LLM 实时调用 -> 本地缓存（历史成功结果）-> 规则抽取（离线兜底）。
任何一级失败都会在文档 fetch_status 上留下标记，绝不静默产出"正常"结论。
"""
import hashlib
import json
import re
from datetime import datetime, timezone

from . import config, db

# ---------- 领域别名表（演示事件范围，见 README 已知边界） ----------

ENTITY_ALIASES = {
    "成飞集团": ["成飞集团", "成都飞机工业", "成飞"],
    "成飞集成": ["成飞集成"],
    "中航电测": ["中航电测", "300114"],
    "左江科技": ["左江科技", "*st左江", "st左江", "左江退", "左江", "300799"],
    "法尔胜": ["法尔胜", "000890"],
    "百利电气": ["百利电气", "600468"],
    "国缆检测": ["国缆检测", "301289"],
    "贵航股份": ["贵航股份"],
    "韩国团队": ["韩国", "lk-99", "lk99"],
}


def canonicalize_subjects(subs) -> list[str]:
    """把 LLM 抽取的任意主体名映射到规范名（最长别名优先，避免'成飞'抢匹配'成飞集成'）。"""
    out = []
    for s in subs or []:
        s_l = str(s).lower().strip()
        best, best_len = None, 0
        for canon, aliases in ENTITY_ALIASES.items():
            for a in aliases:
                a_l = a.lower()
                if a_l and (a_l in s_l or (len(s_l) >= 2 and s_l in a_l)) and len(a_l) > best_len:
                    best, best_len = canon, len(a_l)
        out.append(best or s)
    return list(dict.fromkeys(out))

KEYWORD_BUCKETS = {
    "重组借壳": ["借壳", "重组", "注入", "发行股份购买", "收购"],
    "监管立案": ["立案", "问询", "退市", "摘牌", "风险警示"],
    "室温超导": ["超导", "lk-99", "lk99", "复现"],
}

DENY_KW = ["澄清", "否认", "不属实", "传闻不实", "无此计划", "不存在", "未筹划", "证伪", "未能复现", "与公司无关", "无任何关系"]
CORRECT_KW = ["更正", "修正", "下修", "上修"]
CLOSE_KW = ["摘牌", "终止上市", "退市", "重组完成", "过户完成", "注册生效", "终止重组", "已完成"]

# 来源权威度（1-5），用于证据权重
SOURCE_AUTHORITY = {
    "公司公告": 5, "官方公告": 5, "互动易": 3.5, "权威媒体": 3.5,
    "研报": 3, "一般媒体": 2.5, "社交媒体": 1,
}
# 声明类型的证据权重系数
TYPE_WEIGHT = {"fact": 1.0, "opinion": 0.7, "speculation": 0.55, "rumor": 0.4}

EXTRACT_PROMPT = """你是投资事件情报抽取器。输入一篇金融文档的元数据与正文，输出 JSON。
任务：
1. 识别文中 1-4 条核心主张（claim），每条输出：
   - type: fact(事实/可验证客观陈述，如公告、监管披露) | opinion(观点/分析师的评价判断) | speculation(推测/基于部分事实的未证实推断) | rumor(传闻/来源不明的市场传言，如"据传""网传""消息称")
   - summary: 一句话中文归纳
   - quote: 正文中支撑该主张的原句，必须逐字引用不得改写
   - subjects: 涉事主体名称数组（公司名、机构名）
   - event_time: 该主张涉及事件的发生时间，格式 YYYY-MM-DD，无法确定填 ""
   - core_proposition: 该主张对应的核心命题，一句话，如"成飞集团借壳中航电测"；若是传闻类，注明"（市场传闻）"
2. 分类标准：公告/监管/官方回应的陈述为 fact；分析师研报评价为 opinion；"或将/可能/有望"的推断为 speculation；"据传/网传/消息称/疑似"且无官方来源为 rumor。
3. quote 必须逐字引用原文。
只输出 JSON：{"claims":[...]}"""

MERGE_PROMPT = """你是投资事件合并判定器。判断新主张应并入哪个已有事件，还是创建新事件。
已有事件列表（JSON，含 event_id、title、topic）：{events}
新主张信息在用户消息的 JSON 中给出。
判定规则：若新主张与某事件讨论同一件事（同一主体+同一事件实质，即使结论相反也应合并，如"否认传闻"应并入该传闻事件），输出 merge 并给出该事件 id；否则输出 new。
只输出 JSON：{{"decision":"merge|new","event_id":"...或空","reason":"一句中文理由"}}"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _as_list(x) -> list:
    """entity_tags / subjects 在 DB 中是 JSON 字符串，统一解析为列表（兼容双重编码）。"""
    if isinstance(x, str):
        try:
            parsed = json.loads(x)
            if isinstance(parsed, str):  # 双重编码：再解一层
                parsed = json.loads(parsed)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return list(x) if x else []


def _client():
    from openai import OpenAI

    return OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL, timeout=45)


def _cache_path(doc_id: str):
    return config.EXTRACTED_DIR / f"extract_{doc_id}.json"


def _load_cache(doc_id: str):
    p = _cache_path(doc_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_cache(doc_id: str, payload: dict):
    config.EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    (_cache_path(doc_id)).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_proposition(text: str) -> str:
    """去掉括号注释与空白，便于命题归并。"""
    text = re.sub(r"[（(【\[].*?[）)】\]]", "", text or "")
    return re.sub(r"\s+", "", text).strip()


def _find_subjects(text: str) -> list[str]:
    found = []
    for canon, aliases in ENTITY_ALIASES.items():
        for a in aliases:
            if a.lower() in (text or "").lower():
                found.append(canon)
                break
    return found


def rule_extract_claims(doc: dict) -> list[dict]:
    """离线规则抽取：关键词分类 + 别名主体匹配。精度低于 LLM，仅作降级兜底。"""
    text = f"{doc.get('title', '')}。{doc.get('body', '')}"
    subjects = _find_subjects(text)
    # 命题提示：规则模式无法理解语义，允许文档元数据显式给出核心命题
    prop_hint = doc.get("proposition_hint") or doc.get("title", "")[:50]
    sentences = [s.strip() for s in re.split(r"[。；;\n]", text) if len(s.strip()) > 10]
    claims = []
    kw_hits = []

    def classify(s: str) -> str:
        if re.search(r"传闻|据传|网传|传言|消息称|疑似", s):
            return "rumor"
        if re.search(r"分析师|研报|认为|看好|展望", s):
            return "opinion"
        if re.search(r"或|可能|有望|预计", s):
            return "speculation"
        return "fact"

    for s in sentences[:8]:
        t = classify(s)
        hits = [w for w in DENY_KW + CORRECT_KW + CLOSE_KW if w in s]
        if hits:
            kw_hits.append((s, hits[0]))
        claims.append(
            {
                "type": t,
                "summary": s[:80],
                "quote": s[:200],
                "subjects": subjects,
                "event_time": doc.get("event_time") or "",
                "core_proposition": prop_hint,
            }
        )
    # 澄清/更正/完结类文档：即使未抽出句子也生成一条对应类型主张
    deny = [h for h in kw_hits if h[1] in DENY_KW]
    close = [h for h in kw_hits if h[1] in CLOSE_KW]
    correct = [h for h in kw_hits if h[1] in CORRECT_KW]
    if deny and not any(c["type"] == "fact" and any(k in c["quote"] for k in DENY_KW) for c in claims):
        s = deny[0][0]
        claims.insert(0, {"type": "fact", "summary": "官方否认：" + s[:60], "quote": s[:200],
                          "subjects": subjects, "event_time": doc.get("event_time") or "",
                          "core_proposition": prop_hint})
    for label, arr in (("close", close), ("correct", correct)):
        if arr:
            s = arr[0][0]
            claims.append({"type": "fact", "summary": f"({label})" + s[:60], "quote": s[:200],
                           "subjects": subjects, "event_time": doc.get("event_time") or "",
                           "core_proposition": prop_hint})
    return claims or [{
        "type": "fact", "summary": doc.get("title", ""), "quote": sentences[0][:200] if sentences else doc.get("title", ""),
        "subjects": subjects, "event_time": doc.get("event_time") or "",
        "core_proposition": prop_hint,
    }]


def extract_claims(doc: dict) -> tuple[list[dict], str]:
    """返回 (claims, method)。method ∈ llm|cache|rule。缓存按稳定 doc_id 键控。"""
    doc_id = doc["doc_id"]
    payload = _load_cache(doc_id)
    if payload and payload.get("claims"):
        return payload["claims"], "cache"

    if config.LLM_ENABLED:
        try:
            resp = _client().chat.completions.create(
                model=config.DEEPSEEK_MODEL,
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=2500,
                messages=[
                    {"role": "system", "content": EXTRACT_PROMPT},
                    {"role": "user", "content": json.dumps(
                        {k: doc.get(k) for k in ("title", "source", "publisher", "publish_time", "disclose_time", "event_time", "body")},
                        ensure_ascii=False)},
                ],
            )
            data = json.loads(resp.choices[0].message.content)
            claims = data.get("claims") or []
            if claims:
                # 缓存带文档元信息，便于审计与人工核验
                _save_cache(doc_id, {"claims": claims, "method": "llm", "model": config.DEEPSEEK_MODEL,
                                     "at": now_iso(), "doc_title": doc.get("title", ""),
                                     "doc_disclose_time": doc.get("disclose_time", "")})
                return claims, "llm"
        except Exception:
            pass  # 网络/额度/格式异常 -> 规则降级

    return rule_extract_claims(doc), "rule"


def judge_merge(events: list[dict], claim: dict) -> dict:
    """灰区合并判定：规则打分后仍不明确时交给 LLM；失败则按主体匹配合并。

    缓存键基于候选事件的标题/话题与主张内容（不含随机 event_id），跨运行可复用。
    """
    key = hashlib.md5(
        json.dumps(
            sorted((e.get("title") or "") + "|" + (e.get("topic") or "") for e in events)
            + [claim.get("core_proposition"), str(claim.get("subjects")), claim.get("summary")],
            ensure_ascii=False).encode()
    ).hexdigest()[:16]
    cache = _load_cache(f"merge_{key}")
    if cache and "decision" in cache:
        return cache["decision"]

    result = None
    if config.LLM_ENABLED:
        try:
            resp = _client().chat.completions.create(
                model=config.DEEPSEEK_MODEL,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=300,
                messages=[
                    {"role": "system", "content": MERGE_PROMPT.format(events=json.dumps(events, ensure_ascii=False))},
                    {"role": "user", "content": json.dumps({
                        "type": claim.get("claim_type"), "proposition": claim.get("core_proposition"),
                        "subjects": claim.get("subjects"), "summary": claim.get("summary"),
                    }, ensure_ascii=False)},
                ],
            )
            data = json.loads(resp.choices[0].message.content)
            if data.get("decision") in ("merge", "new"):
                result = data
                _save_cache(f"merge_{key}", {"decision": result, "at": now_iso()})
        except Exception:
            result = None
    if result is None:
        # 降级：主体交集或话题关键词一致即合并到第一个候选事件
        claim_text = " ".join(str(x) for x in _as_list(claim.get("subjects"))) + " " + str(claim.get("core_proposition", ""))
        bucket = next((b for b, kws in KEYWORD_BUCKETS.items() if any(k in claim_text.lower() for k in kws)), "")
        cand = None
        for e in events:
            if set(_as_list(e.get("entity_tags"))) & set(_as_list(claim.get("subjects"))):
                cand = e
                break
            # 话题匹配：用桶内关键词匹配话题文本，而不是桶名本身
            if bucket and any(k in (e.get("topic") or "").lower() for k in KEYWORD_BUCKETS[bucket]):
                cand = e
                break
        result = {"decision": "merge", "event_id": cand["event_id"], "reason": "降级规则：主体/话题交集"} if cand else {
            "decision": "new", "event_id": "", "reason": "降级规则：无主体或话题交集"}
    return result
