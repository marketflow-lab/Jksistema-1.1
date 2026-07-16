#!/bin/sh
set -eu

: "${PUBLIC_SIP_HOST:?PUBLIC_SIP_HOST obrigatório}"
: "${PUBLIC_IPV4:?PUBLIC_IPV4 obrigatório}"
: "${OPENAI_PROJECT_ID:?OPENAI_PROJECT_ID obrigatório}"
: "${META_SIP_ALLOWED_CIDRS:?META_SIP_ALLOWED_CIDRS obrigatório}"
: "${SIP_TLS_CERT:?SIP_TLS_CERT obrigatório}"
: "${SIP_TLS_KEY:?SIP_TLS_KEY obrigatório}"

test -r "$SIP_TLS_CERT"
test -r "$SIP_TLS_KEY"

envsubst '${PUBLIC_SIP_HOST} ${PUBLIC_IPV4} ${OPENAI_PROJECT_ID}' \
  < /etc/kamailio/kamailio.cfg.template \
  > /etc/kamailio/kamailio.cfg
envsubst '${SIP_TLS_CERT} ${SIP_TLS_KEY}' \
  < /etc/kamailio/tls.cfg.template \
  > /etc/kamailio/tls.cfg

trusted_file=/etc/kamailio/trusted-cidrs.cfg
: > "$trusted_file"
old_ifs=$IFS
IFS=','
for cidr in $META_SIP_ALLOWED_CIDRS; do
  cidr=$(printf '%s' "$cidr" | tr -d '[:space:]')
  case "$cidr" in
    ''|'0.0.0.0/0'|'::/0') echo "CIDR SIP inseguro: $cidr" >&2; exit 64 ;;
  esac
  printf 'if (is_in_subnet("$si", "%s")) return;\n' "$cidr" >> "$trusted_file"
done
IFS=$old_ifs

exec kamailio -DD -E -f /etc/kamailio/kamailio.cfg
