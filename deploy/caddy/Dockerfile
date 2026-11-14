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
