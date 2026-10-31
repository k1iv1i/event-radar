FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .
ENV DATA_DIR=/app/data
ENV DB_PATH=/app/data/timeline.db

# 用缓存抽取结果重建演示库（不依赖运行时 LLM 调用，接口失败自动降级）
RUN python -m scripts.bootstrap_demo --offline || true

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
