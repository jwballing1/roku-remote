#!/bin/bash
# Starts the hub as an ordinary user. The hub starts Caddy itself once it has been set up.

# Give the hub's user the data folder, whatever owns the NAS folder mapped there.
mkdir -p /data
chown -R 1000:1000 /data 2>/dev/null || echo "Couldn't take ownership of /data. If the hub can't save, check that folder's permissions."

exec setpriv --reuid=1000 --regid=1000 --init-groups python /app/hub/hub.py
