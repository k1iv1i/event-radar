"""测试基座：关闭 LLM（走规则/缓存抽取），每个用例重置数据库，保证确定性。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402

config.LLM_ENABLED = False


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.reset_db()
    yield


def make_doc(title, body, source="一般媒体", publisher="测试媒体", publish_time="2025-01-01",
             event_time="", proposition_hint=""):
    from app import pipeline
    meta = {"title": title, "source": source, "publisher": publisher,
            "publish_time": publish_time, "event_time": event_time,
            "proposition_hint": proposition_hint, "url": "https://example.com/doc"}
    return pipeline.ingest_document(meta, body)
