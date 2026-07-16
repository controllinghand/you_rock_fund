ARG IB_GATEWAY_TAG=latest
FROM ghcr.io/gnzsnz/ib-gateway:${IB_GATEWAY_TAG}

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && rm -rf /var/lib/apt/lists/*

# Stash a copy of the freshly-installed TWS/Gateway tree OUTSIDE the volume mount
# point (/home/ibgateway/Jts is a persistent named volume at runtime). When the base
# image bumps the TWS version on a rebuild, the stale volume shadows the new jars and
# IBC dies with "version <X> is not installed: can't find jars folder" (exit 4). The
# entrypoint copies the missing version out of this bake into the volume on startup.
RUN cp -a /home/ibgateway/Jts /opt/tws-baked

COPY entrypoint.sh /usr/local/bin/yrvi-gw-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/yrvi-gw-entrypoint.sh \
    && chmod +x /usr/local/bin/yrvi-gw-entrypoint.sh

USER 1000:1000
