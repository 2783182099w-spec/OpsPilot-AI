ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=opspilot_api.settings.container
WORKDIR /app
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements-modern.txt .
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements-modern.txt && useradd -m -u 1000 app
COPY . .
RUN mkdir -p /app/data /app/logs /app/monitor /prometheus-data && chown -R app:app /app /prometheus-data
USER app
EXPOSE 8000
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "opspilot_api.routing:application"]
