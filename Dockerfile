FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cartulary ./cartulary
COPY web ./web
EXPOSE 8000
CMD ["uvicorn","cartulary.main:app","--host","0.0.0.0","--port","8000","--workers","1"]
