# Caddy ohne root: Datenverzeichnisse gehören Benutzer 10001, Caddy lauscht
# intern auf 8080/8443 (siehe Caddyfile), Docker veröffentlicht 80/443.
FROM caddy:2.11.7

RUN mkdir -p /data/caddy /config/caddy \
 && chown -R 10001:10001 /data /config
USER 10001:10001
