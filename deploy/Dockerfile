FROM python:3.12-slim

# Nur eine Laufzeitabhängigkeit: websockets. Der Rest ist Standardbibliothek.
RUN pip install --no-cache-dir "websockets==15.0.1" \
 && useradd --create-home --uid 10001 agent \
 && mkdir /audit && chown agent /audit

WORKDIR /app
COPY voiceagent/ voiceagent/
USER agent
EXPOSE 8080 9100
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9100/healthz', timeout=2)"
CMD ["python", "-m", "voiceagent.server", "--port", "8080", "--metrics-port", "9100"]
