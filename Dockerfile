FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY study ./study
RUN useradd --create-home app && mkdir /app/data && chown app:app /app/data
USER app
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "study.wsgi:app"]
