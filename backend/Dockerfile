FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ffmpeg for video transcoding, curl for healthcheck
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8000

# --workers: rate-limit and reminder state is shared (Redis / Postgres advisory
#   lock + ON CONFLICT claims), so workers no longer multiply either. Override
#   with WEB_CONCURRENCY; 1 restores single-process behavior.
# --proxy-headers + --forwarded-allow-ips: the API sits behind the Cloudflare
#   tunnel, so without these request.client.host is the tunnel's address for
#   every caller. Scoped to the tunnel's own peer address rather than "*":
#   port 8000 is published on the host, so a wildcard would let anything that
#   can reach the host forge its apparent client IP. uvicorn 0.30 matches exact
#   addresses only (no CIDR), hence a concrete value, overridable per
#   deployment. Rate limits key on CF-Connecting-IP independently of this.
ENV WEB_CONCURRENCY=4
ENV FORWARDED_ALLOW_IPS=172.20.0.1
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-4} --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
