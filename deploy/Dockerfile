# Sprachagent als Container: Paket als Wheel bauen, im Laufzeit-Image ohne
# Build-Werkzeuge installieren, als Benutzer 10001 ausführen.
FROM python:3.13.18-slim-bookworm AS bau
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY gw_contactcenter/ gw_contactcenter/
RUN pip install --no-cache-dir "build==1.6.1" && python -m build --wheel --outdir /wheel

FROM python:3.13.18-slim-bookworm
COPY --from=bau /wheel /wheel
RUN pip install --no-cache-dir /wheel/*.whl "websockets==17.3" \
 && rm -rf /wheel \
 && useradd --no-create-home --uid 10001 --user-group agent \
 && mkdir /audit && chown agent:agent /audit
USER 10001:10001
EXPOSE 8080 9100
HEALTHCHECK --interval=15s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9100/healthz', timeout=2)"]
CMD ["gw-contactcenter", "server"]
