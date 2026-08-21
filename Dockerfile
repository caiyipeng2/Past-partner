FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PAST_PARTNER_HOST=0.0.0.0 \
    PAST_PARTNER_PORT=8080 \
    PAST_PARTNER_DATA_DIR=/var/lib/past-partner/data

WORKDIR /opt/past-partner
COPY requirements-core.txt ./requirements-core.txt
RUN python -m pip install --no-cache-dir -r requirements-core.txt
COPY src ./src
COPY web ./web

RUN mkdir -p /var/lib/past-partner/data
EXPOSE 8080
CMD ["python", "-m", "src.server"]
