FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY analytics/ /app/analytics/
COPY frontend/ /app/frontend/
COPY database/ipcc_litdb.sqlite.gz database/snapshot.json /app/database/
COPY database/export_for_postgres.py /app/database/export_for_postgres.py
COPY database/schema/ /app/database/schema/
COPY database/migrations/ /app/database/migrations/
COPY scripts/ /app/scripts/
COPY data/ /app/data/

EXPOSE 8077
CMD ["python", "scripts/run_server.py"]
