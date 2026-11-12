# Caddy with the layer4 app (caddy-l4), so one public :443 carries both HTTPS and TURN over
# TLS. A browser on a network that blocks UDP and every port but 443 -- a university or
# corporate Wi-Fi, which is where the demo is shown -- can still reach the voice call by
# relaying through TURN/TLS on 443, and only the SNI tells that connection apart from a
# page load. Stock Caddy cannot route on SNI below HTTP, hence the custom build.
#
# Built on the host by `deploy.sh` (`docker compose build caddy`), not pushed: it changes
# only when these two versions do, and the build cache makes every later deploy a no-op.
ARG CADDY_VERSION=2.11.9

FROM caddy:${CADDY_VERSION}-builder AS build
# A tag, not a branch: a deploy must not pick up whatever caddy-l4 merged that morning.
RUN xcaddy build --with github.com/mholt/caddy-l4@v0.1.2

FROM caddy:${CADDY_VERSION}-alpine
COPY --from=build /usr/bin/caddy /usr/bin/caddy

# Not root at run time (Trivy DS-0002). Binding :80 and :443 needs no root: the file
# capability below grants exactly that, as the stock image does for its own binary (a COPY
# drops it). UID 10002 owns the certificate store; deploy.sh gives the host directory
# mounted at /data to the same UID.
RUN apk add --no-cache libcap \
    && setcap cap_net_bind_service=+ep /usr/bin/caddy \
    && addgroup -S -g 10002 caddy \
    && adduser -S -D -H -u 10002 -G caddy caddy \
    && chown -R caddy:caddy /data /config
USER caddy
