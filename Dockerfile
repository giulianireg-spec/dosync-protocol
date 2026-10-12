# DoSync hub — container image (also the Home Assistant add-on's image).
#
# Builds and installs the PACKAGE rather than copying loose scripts, so the
# image contains exactly what `pip install "dosync[mcp]"` gives anyone else. One
# artifact, one behavior. Published for amd64 and arm64 (a Raspberry Pi) by
# .github/workflows/release.yml:
#
#   docker run -d --name dosync -p 47200:47200 -v dosync-data:/data \
#     ghcr.io/giulianireg-spec/dosync-protocol:latest
#   docker logs dosync          # the hub's token, printed on first start
#
# On Linux, add --network host so discovery (mDNS, SSDP, broadcast) sees your
# network; a bridged container only sees itself.
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY dosync/ ./dosync/
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim
LABEL org.opencontainers.image.title="DoSync hub" \
      org.opencontainers.image.description="Governance between AI agents and physical devices (SAMUEL reference hub)" \
      org.opencontainers.image.source="https://github.com/giulianireg-spec/dosync-protocol" \
      org.opencontainers.image.licenses="Apache-2.0"
WORKDIR /app
COPY --from=build /src/dist/*.whl /tmp/
# [mcp]: so `docker exec dosync dosync-mcp --print-config` and the MCP server
# over HTTP work from the same image.
RUN WHEEL=$(ls /tmp/*.whl) && pip install --no-cache-dir "${WHEEL}[mcp]" && rm /tmp/*.whl

# Everything the hub writes lives here: database, certificates, state, and
# (as a Home Assistant add-on) options.json. Mount a volume on it.
VOLUME /data
# DOSYNC_DB, the name the hub reads (DOSYNC_DB_PATH sent the chain outside the
# volume until 2026-07-22). dosync-container defaults the rest under /data.
ENV DOSYNC_DB=/data/dosync.db

# Authentication is ON (the hub's default): a container listens on the network.
# The token is printed in the log on first start. For a throwaway demo only:
#   -e DOSYNC_AUTH=false
EXPOSE 47200

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,sys; \
r=urllib.request.urlopen('http://127.0.0.1:47200/.well-known/wot', timeout=4); sys.exit(0 if r.status==200 else 1)"

CMD ["dosync-container"]
