FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 HF_HOME=/models DB_PATH=/data/esg.db CACHE_DIR=/data/cache
COPY . .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 && pip install --no-cache-dir -e '.[nlp]' && python -m spacy download en_core_web_sm
EXPOSE 8000
CMD ["uvicorn", "esg_lens.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
