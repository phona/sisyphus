FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# adb client：M2 ADB driver (#238) 调用 `adb -s <ep> shell ...`，需要 client 二进制；
# playwright/http driver 不需要但 thin overhead 可接受。
RUN apt-get update \
    && apt-get install -y --no-install-recommends adb \
    && apt-get clean

WORKDIR /opt/thanatos

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Stdio MCP server.
ENTRYPOINT ["python", "-m", "thanatos.server"]
