#!/bin/sh
set -eu

metadata() {
  curl -fsS -H 'Metadata-Flavor: Google' \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}

PUBLIC_SIP_HOST="$(metadata voice-sip-host)"
case "$PUBLIC_SIP_HOST" in
  ''|*[!A-Za-z0-9.-]*) echo "voice-sip-host invalido" >&2; exit 64 ;;
esac

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates certbot curl docker-compose docker.io git openssl
systemctl enable --now docker

if [ ! -s "/etc/letsencrypt/live/$PUBLIC_SIP_HOST/fullchain.pem" ]; then
  certbot certonly --standalone --non-interactive --agree-tos \
    --register-unsafely-without-email \
    --preferred-challenges http \
    -d "$PUBLIC_SIP_HOST"
fi

install -d -m 0750 /opt/jk-whatsapp-voice/secrets/tls
install -m 0644 "/etc/letsencrypt/live/$PUBLIC_SIP_HOST/fullchain.pem" \
  /opt/jk-whatsapp-voice/secrets/tls/fullchain.pem
install -m 0600 "/etc/letsencrypt/live/$PUBLIC_SIP_HOST/privkey.pem" \
  /opt/jk-whatsapp-voice/secrets/tls/privkey.pem

cat >/etc/cron.d/jk-whatsapp-voice-cert-renew <<EOF
17 4 * * * root certbot renew --quiet --deploy-hook '/usr/bin/install -m 0644 /etc/letsencrypt/live/$PUBLIC_SIP_HOST/fullchain.pem /opt/jk-whatsapp-voice/secrets/tls/fullchain.pem && /usr/bin/install -m 0600 /etc/letsencrypt/live/$PUBLIC_SIP_HOST/privkey.pem /opt/jk-whatsapp-voice/secrets/tls/privkey.pem && /usr/bin/docker-compose -f /opt/jk-whatsapp-voice/docker-compose.yml restart kamailio'
EOF
chmod 0644 /etc/cron.d/jk-whatsapp-voice-cert-renew

touch /opt/jk-whatsapp-voice/bootstrap-ready
