FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY study ./study
RUN useradd --uid 10001 --create-home app && mkdir /app/data && chown app:app /app/data && chmod 700 /app/data
USER app
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "30", "--graceful-timeout", "30", "--error-logfile", "-", "study.wsgi:app"]
