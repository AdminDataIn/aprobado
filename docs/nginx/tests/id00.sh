#!/bin/sh
# Run only through id00_local.py in a disposable container without network.
set -eu

test "${ID00_TEST_CONTAINER:-}" = 1
media=/var/www/aprobado/media
log=/var/log/nginx/access.log
cfg=/id00/tests/id00.conf
doc=/documentos/libranza/0/cedula_frontal/
hosts='aprobado.com.co emprender.aprobado.com.co contratistas.aprobado.com.co market.aprobado.com.co'
mkdir -p "$media/marketplace/items"
printf 'ID00-PDF-SINTETICO' > "$media/fixture-id00.pdf"
printf 'ID00-PUBLICO-SINTETICO' > "$media/marketplace/items/fixture-id00.txt"
cp /fixtures/base.conf /tmp/id00-vhosts.conf
nginx -t -c "$cfg"
nginx -c "$cfg"

respuesta() {
    esquema=$1; host=$2; ruta=$3; shift 3
    puerto=80
    if [ "$esquema" = https ]; then puerto=443; fi
    curl -sS --max-time 6 --noproxy '*' --path-as-is --cacert /fixtures/cert.pem \
        --resolve "$host:$puerto:127.0.0.1" -D /tmp/id00-headers -o /tmp/id00-body \
        -w '%{http_code}' "$@" "$esquema://$host$ruta"
}

esperar() {
    codigo=$1; shift
    real=$(respuesta "$@")
    if [ "$real" != "$codigo" ]; then printf 'Estado esperado %s, obtenido %s\n' "$codigo" "$real" >&2; exit 1; fi
}

alias_control() {
    tag=$1
    esperar 301 http www.aprobado.com.co "$doc?fixture=ID00-ALIAS-HTTP-$tag"
    grep -qi "^Location: https://www.aprobado.com.co$doc?fixture=ID00-ALIAS-HTTP-$tag" /tmp/id00-headers
    esperar 301 https www.aprobado.com.co "$doc?fixture=ID00-ALIAS-HTTPS-$tag"
    grep -qi "^Location: https://aprobado.com.co$doc?fixture=ID00-ALIAS-HTTPS-$tag" /tmp/id00-headers
    for p in HTTP HTTPS; do
        test "$(grep -c "GET .*ID00-ALIAS-$p-$tag .* 301 " "$log")" -eq 1
    done
}

alias_control BASE
for h in $hosts; do
    esperar 301 http "$h" "$doc?fixture=ID00-BASE-HTTP"
    esperar 404 https "$h" "$doc?fixture=ID00-BASE-HTTPS"
done
test "$(grep -c 'ID00-BASE-HTTP ' "$log")" -eq 4
test "$(grep -c 'ID00-BASE-HTTPS ' "$log")" -eq 4

cp /fixtures/candidato.conf /tmp/id00-vhosts.conf
nginx -t -c "$cfg"
nginx -s reload -c "$cfg"
sleep 1

alias_control CANDIDATO
for h in $hosts; do
    esperar 301 http "$h" "$doc?fixture=ID00-QUERY-PRIVADO" -I
    grep -qi "^Location: https://$h$doc?fixture=ID00-QUERY-PRIVADO" /tmp/id00-headers
    esperar 404 https "$h" "$doc?fixture=ID00-QUERY-PRIVADO"
    esperar 404 https "$h" "$doc?fixture=ID00-OTRO-PRIVADO" -H 'X-Fixture-Role: other'
    esperar 404 https "$h" '/doc%75mentos/libranza/0/cedula_frontal/?fixture=ID00-ENCODED-PRIVADO'
    esperar 404 https "$h" '/captura-documental/ID00-CAPTURA-PRIVADA/'
    esperar 404 https "$h" '/api/pagares/download/ID00-CAPACIDAD-SINTETICA/?fixture=ID00-QUERY-PRIVADO'
    esperar 404 https "$h" /media/fixture-id00.pdf
    esperar 404 https "$h" /_protected_legacy/fixture-id00.pdf
    esperar 200 https "$h" "$doc?fixture=ID00-TITULAR-PRIVADO" -H 'X-Fixture-Role: owner'
    cmp "$media/fixture-id00.pdf" /tmp/id00-body
    grep -qi '^Cache-Control: private, no-store' /tmp/id00-headers
    esperar 200 https "$h" /media/marketplace/items/fixture-id00.txt
    cmp "$media/marketplace/items/fixture-id00.txt" /tmp/id00-body
    esperar 301 http "$h" '/?fixture=ID00-ORDINARIO-HTTP'
    esperar 200 https "$h" '/?fixture=ID00-ORDINARIO-HTTPS'
    test "$(grep -Fc " $h " "$log" | tr -d ' ')" -ge 6
    test "$(grep '^ID00 ' "$log" | grep -Fc " $h documento")" -eq 4
    grep -F " $h documento" "$log" | grep -Eq '^ID00 [a-f0-9]{32} HEAD 301 [0-9.]+ '
    grep -F " $h documento" "$log" | grep -Eq '^ID00 [a-f0-9]{32} GET 404 [0-9.]+ '
    grep -Fq " $h captura" "$log"
    grep -Fq " $h descarga_temporal" "$log"
done

test "$(grep -c '^ID00 ' "$log")" -eq 24
test "$(grep -c 'GET /?fixture=ID00-ORDINARIO-HTTP ' "$log")" -eq 4
test "$(grep -c 'GET /?fixture=ID00-ORDINARIO-HTTPS ' "$log")" -eq 4
test "$(grep -c 'GET /media/marketplace/items/fixture-id00.txt ' "$log")" -eq 4
if grep -Eq 'ID00-QUERY-PRIVADO|ID00-OTRO-PRIVADO|ID00-ENCODED-PRIVADO|ID00-CAPTURA-PRIVADA|ID00-CAPACIDAD-SINTETICA|ID00-TITULAR-PRIVADO' "$log"; then
    echo 'El registro documental conserva campos sensibles' >&2
    exit 1
fi
if grep -Eq 'ID00-QUERY-PRIVADO|ID00-OTRO-PRIVADO|ID00-ENCODED-PRIVADO|ID00-CAPTURA-PRIVADA|ID00-CAPACIDAD-SINTETICA|ID00-TITULAR-PRIVADO' /var/log/nginx/error.log; then
    echo 'El error log de estas sondas contiene la marca documental' >&2
    exit 1
fi

cp /fixtures/base.conf /tmp/id00-vhosts.conf
nginx -t -c "$cfg"
nginx -s reload -c "$cfg"
sleep 1
alias_control REVERSION
for h in $hosts; do
    esperar 301 http "$h" "$doc?fixture=ID00-REVERSION-HTTP"
    esperar 404 https "$h" "$doc?fixture=ID00-REVERSION-HTTPS"
    esperar 404 https "$h" /media/fixture-id00.pdf
    esperar 200 https "$h" "$doc" -H 'X-Fixture-Role: owner'
    cmp "$media/fixture-id00.pdf" /tmp/id00-body
done
test "$(grep -c 'ID00-REVERSION-HTTP ' "$log")" -eq 4
test "$(grep -c 'ID00-REVERSION-HTTPS ' "$log")" -eq 4
test "$(grep -c '^ID00 ' "$log")" -eq 24
echo 'NGINX_ID00=PASS HOSTS=4/4 ALIAS=INTACTO HTTP_HTTPS=PASS TITULAR_AJENO_ANONIMO=PASS REVERSION=PASS'
