#!/bin/sh
# Run only in a disposable container with /id00 mounted read-only and no network.
set -eu

base=http://127.0.0.1:8080
media=/var/www/aprobado/media
log=/var/log/nginx/access.log
mkdir -p "$media/marketplace/items"
printf 'ID00-PDF-SINTETICO' > "$media/fixture-id00.pdf"
printf 'ID00-PUBLICO-SINTETICO' > "$media/marketplace/items/fixture-id00.txt"
nginx -t -c /id00/tests/id00.conf
nginx -c /id00/tests/id00.conf

denegado() {
    if wget -S -O /dev/null "$base$1" >/tmp/id00-status 2>&1; then
        echo 'Una ruta que debia denegarse devolvio exito' >&2
        exit 1
    fi
    grep -q '404 Not Found' /tmp/id00-status
}

denegado /media/fixture-id00.pdf
denegado /_protected_legacy/fixture-id00.pdf
denegado '/documentos/libranza/0/cedula_frontal/?fixture=ID00-QUERY-PRIVADO'
denegado '/doc%75mentos/libranza/0/cedula_frontal/?fixture=ID00-ENCODED-PRIVADO'
denegado '/captura-documental/ID00-CAPTURA-PRIVADA/'
denegado '/api/pagares/download/ID00-CAPACIDAD-SINTETICA/'

wget -S -O /tmp/id00-owner --header 'X-Fixture-Owner: yes' \
    "$base/documentos/libranza/0/cedula_frontal/" 2>/tmp/id00-headers
cmp "$media/fixture-id00.pdf" /tmp/id00-owner
grep -qi 'Cache-Control: private, no-store' /tmp/id00-headers
wget -q -O /tmp/id00-public "$base/media/marketplace/items/fixture-id00.txt"
cmp "$media/marketplace/items/fixture-id00.txt" /tmp/id00-public
wget -q -O /dev/null "$base/?fixture=ID00-ORDINARIO"

test "$(grep -c '^ID00 ' "$log")" -eq 4
grep -q 'GET /?fixture=ID00-ORDINARIO' "$log"
grep -q 'GET /media/marketplace/items/fixture-id00.txt' "$log"
if grep -Eq 'ID00-QUERY-PRIVADO|ID00-ENCODED-PRIVADO|ID00-CAPTURA-PRIVADA|ID00-CAPACIDAD-SINTETICA' "$log"; then
    echo 'El registro documental conserva campos sensibles' >&2
    exit 1
fi
echo 'NGINX_ID00=PASS (config, 6 denegaciones, X-Accel, no-store, media publico, logs separados)'
