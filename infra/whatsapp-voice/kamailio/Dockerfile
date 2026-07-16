FROM debian:12-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates gettext-base kamailio kamailio-extra-modules kamailio-tls-modules netbase \
    && rm -rf /var/lib/apt/lists/*

COPY kamailio.cfg.template /etc/kamailio/kamailio.cfg.template
COPY tls.cfg.template /etc/kamailio/tls.cfg.template
COPY entrypoint.sh /usr/local/bin/jk-voice-entrypoint
RUN chmod 0755 /usr/local/bin/jk-voice-entrypoint

ENTRYPOINT ["/usr/local/bin/jk-voice-entrypoint"]
