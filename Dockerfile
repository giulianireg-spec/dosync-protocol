# DoSync hub — container image.
#
# Builds and installs the PACKAGE rather than copying loose scripts, so the
# image contains exactly what `pip install dosync` gives anyone else. One
# artifact, one behavior: what runs here is what runs on a user's machine.
FROM python:3.11-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY dosync/ ./dosync/
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.11-slim
WORKDIR /app
COPY --from=build /src/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
COPY dashboard.html .

RUN mkdir -p /data

# Defaults are for TRYING IT OUT, not for production: auth is off and there is
# no TLS. A real deployment sets DOSYNC_AUTH=true, provides DOSYNC_TOKEN, and
# terminates TLS (see the deployment guide).
ENV DOSYNC_AUTH=false
ENV DOSYNC_DB_PATH=/data/dosync.db

EXPOSE 47200

# 0.0.0.0 because a container must accept connections from outside itself; the
# installed console script defaults to loopback, which is the right default for
# a laptop but wrong here.
CMD ["dosync-hub", "--host", "0.0.0.0", "--port", "47200"]
