FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/thanatos

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Stdio MCP server. Drivers (playwright/adb-tools/etc.) are *not* installed in M0:
# all driver methods raise NotImplementedError, so adding the heavy runtime now
# would just bloat the image with unused capability.
ENTRYPOINT ["python", "-m", "thanatos.server"]
