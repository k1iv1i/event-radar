FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .
ENV DATA_DIR=/app/data
ENV DB_PATH=/app/data/timeline.db

# 演示库已随仓库提交（data/timeline.db）；仅当缺失时才用缓存抽取结果重建
RUN if [ ! -f data/timeline.db ]; then python -m scripts.bootstrap_demo --offline || true; fi

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
