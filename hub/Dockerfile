# TV Remote family hub, all in one image: the hub, plus Caddy for HTTPS. No settings are inside:
# they're entered on the setup page (port 8080, home network only) and kept in /data.
# Build it with build-image.ps1, or from the repository root: docker build -f hub/Dockerfile -t tvremote-hub .
FROM caddy:2 AS caddy

FROM python:3.12-slim
COPY --from=caddy /usr/bin/caddy /usr/bin/caddy

# Lets Caddy use port 80 for certificate checks while running as an ordinary user.
RUN apt-get update && apt-get install -y --no-install-recommends libcap2-bin \
    && setcap cap_net_bind_service=+ep /usr/bin/caddy \
    && apt-get purge -y libcap2-bin && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HUB_DATA=/data HUB_WEB_DIR=/app/web HUB_HOST=127.0.0.1 HUB_PORT=8000 \
    HUB_SETUP_PORT=8080 HUB_MANAGE_CADDY=1 \
    XDG_DATA_HOME=/data XDG_CONFIG_HOME=/tmp/caddy-config

COPY hub/hub.py hub/portal.html hub/setup.html hub/start.sh /app/hub/
COPY web/index.html web/relay_rules.py /app/web/

RUN useradd --uid 1000 --no-create-home hub \
    && sed -i 's/\r$//' /app/hub/start.sh && chmod 755 /app/hub/start.sh \
    && mkdir /data

# Settings, accounts, homes and HTTPS certificates. Map a NAS folder here so they survive recreating the container.
VOLUME /data
# 8443: the family hub, over HTTPS. 80: certificate checks. 8080: the setup page, for the home network only.
EXPOSE 8443 80 8080
CMD ["/app/hub/start.sh"]
