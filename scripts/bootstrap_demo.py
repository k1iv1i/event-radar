"""演示数据引导：按披露时间顺序回放入库 data/raw/*.json，重建完整时间线与事件状态。

用法：
  python -m scripts.bootstrap_demo           # 优先实时 LLM 抽取（结果缓存），失败降级
  python -m scripts.bootstrap_demo --offline # 仅用缓存抽取结果，不调用 LLM
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, pipeline  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="仅用缓存抽取结果，不调用 LLM")
    args = ap.parse_args(argv if argv is not None else [])
    if args.offline:
        config.LLM_ENABLED = False

    files = sorted(config.RAW_DIR.glob("*.json"), key=lambda p: p.name)
    if not files:
        print(f"[bootstrap] 未找到原始文档：{config.RAW_DIR}")
        return 1

    db.reset_db()
    print(f"[bootstrap] 回放 {len(files)} 篇文档（LLM={'off' if args.offline else 'on'}）")
    for p in files:
        meta = json.loads(p.read_text(encoding="utf-8"))
        body = meta.pop("body", "")
        res = pipeline.ingest_document(meta, body)
        print(f"  - {p.stem}: {res['claims']} 条主张 ({res['extraction_method']}), "
              f"{len(res['notifications'])} 条通知")

    print("\n[bootstrap] 事件总览：")
    for e in db.list_events():
        concl = json.loads(e.get("conclusion") or "{}")
        props = concl.get("propositions") or {}
        print(f"  [{e['status']:10}] {e['title']}")
        for p in props.values():
            print(f"      · {p['verdict']:10} {p['proposition']}（依据 {p['by_source'] or '—'} {p['by_time'][:10] if p['by_time'] else ''}）")

    print(f"\n[bootstrap] 通知 {len(db.list_notifications())} 条，版本 {sum(len(db.list_versions(e['event_id'])) for e in db.list_events())} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
