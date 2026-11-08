FROM python:3.12-slim
ARG POWERCONTEXT_REV=9db1753434162c0aa7c9f1189a1722e617880a52
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "opentelemetry-api>=1.30,<2" "powercontext[builtin] @ git+https://github.com/oceanbase/powercontext.git@${POWERCONTEXT_REV}"
WORKDIR /app
COPY memory_server.py .
CMD ["python", "-u", "memory_server.py", "--host", "0.0.0.0", "--db", "/data/memory.db"]
