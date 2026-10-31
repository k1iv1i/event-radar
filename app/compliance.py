"""合规边界：禁止投资建议/涨跌预测用语，结论模板保持中性、可追溯。"""
import re

# 投资建议与涨跌预测类用语（出现在结论中即为违规）
ADVICE_PATTERNS = [
    r"建议买入", r"建议卖出", r"建议增持", r"建议减持", r"强烈推荐", r"买入评级",
    r"卖出评级", r"目标价", r"看涨", r"看跌", r"必涨", r"必跌", r"翻倍", r"抄底",
    r"上车", r"满仓", r"梭哈", r"收益承诺", r"稳赚",
]

DISCLAIMER = (
    "本产品仅呈现公开信息的证据状态与时间线，事实、观点、推测与传闻均与来源逐条绑定；"
    "不构成任何投资建议，不对证券价格走势作确定性预测。"
)

STATUS_CN = {
    "emerging": "传闻阶段",
    "developing": "发展中",
    "confirmed": "已证实",
    "denied": "已否认",
    "corrected": "已更正",
    "disputed": "证据冲突",
    "closed": "已完结",
    "expired": "已过期",
}

VERDICT_CN = {
    "confirmed": "已证实",
    "denied": "已否认",
    "corrected": "已更正",
    "disputed": "证据冲突",
    "unverified": "待证实",
    "closed": "已完结",
}


def contains_advice(text: str) -> bool:
    return any(re.search(p, text) for p in ADVICE_PATTERNS)


def sanitize(text: str) -> str:
    """剔除结论文本中的建议/预测语句，返回清洗后的文本。"""
    for p in ADVICE_PATTERNS:
        text = re.sub(p, "【已屏蔽建议性表述】", text)
    return text


def build_statement(event: dict, propositions: list[dict], counts: dict | None = None) -> str:
    """由事件状态与命题证据生成中性结论陈述（模板化、可追溯、无建议）。"""
    status_cn = STATUS_CN.get(event["status"], event["status"])
    latest = event["latest_disclose"] or "未知"
    lines = [f"截至 {latest[:10]}，事件「{event['title']}」整体状态为【{status_cn}】。"]
    for p in propositions:
        v = VERDICT_CN.get(p["verdict"], p["verdict"])
        lines.append(f"- 命题「{p['proposition']}」：{v}（依据：{p['by_source']}，{p['by_time'][:10]}，证据权重 {p['by_weight']}）")
    counts = counts or {}
    lines.append(
        "证据构成：事实 {fact} 条 / 观点 {opinion} 条 / 推测 {speculation} 条 / 传闻 {rumor} 条。".format(
            **{k: counts.get(k, 0) for k in ("fact", "opinion", "speculation", "rumor")}
        )
    )
    lines.append(DISCLAIMER)
    text = "\n".join(lines)
    return sanitize(text)
