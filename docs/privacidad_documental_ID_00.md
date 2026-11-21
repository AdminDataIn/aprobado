# APROBADO-ID-00: privacidad documental historica, fase 1

Estado: P0, EN IMPLEMENTACION hasta UAT Nginx/VPS. No hubo deploy ni traslado de archivos.
Existe exposicion potencial historica de /media/, sin evidencia confirmada de acceso indebido.

## Arquitectura y compatibilidad

Objeto y campo en lista cerrada -> relacion/actor -> autorizacion -> archivo -> entrega.
Ruta comun a los cuatro hosts: `/documentos/<recurso>/<objeto_id>/<tipo>/`.
`objeto_id` es PK del modelo indicado, no numero de credito ni nombre del archivo.
Tipo es uno de los campos permitidos en `services/documentos_privados.py:DOCUMENTOS`.
No se aceptan paths de cliente. La ruta antigua de preview con `?path=` devuelve 404.
La URL del nuevo endpoint permite preview inline seguro de PDF/imagenes y descarga
attachment para formatos no permitidos inline, con nombre neutro, no-store y nosniff.
Los nombres conocidos de captura y documentos Prestadores se conservan sin PII.

| Recurso/modelo | Archivo | Propietario | Pagador | Staff interno activo |
|---|---|---|---|---|
| libranza/CreditoLibranza | cedulas | Titular del credito | Activo, misma empresa | view_identity_documents |
| libranza/CreditoLibranza | laboral, bancario, nomina, contrato | Titular | Activo, misma empresa | view_credito o view_creditolibranza |
| emprendimiento/CreditoEmprendimiento, imagen-negocio/ImagenNegocio | Evidencia negocio | Titular | Solo si credito tiene empresa relacionada | view_credito o view del modelo correspondiente |
| pago/HistorialPago, historial/HistorialEstado, pagare/Pagare | Comprobante, generado/firmado | Titular | Activo, empresa del credito | view_credito o view del modelo correspondiente |
| lote/LotePagoEmpresa | Excel/comprobante agrupado | No cliente individual | Activo, misma empresa | view_lotepagoempresa |
| movimiento/MovimientoAhorro | Comprobante | Titular cuenta | No | view_movimientoahorro |
| comision/PagoComisionEjecutivo | Comprobante | Asesor activo titular | No | view_pagocomisionejecutivo |
| prestador/ContractorApplicationDocument | Documento solicitud | Solicitante | No antes de originacion | contractors.can_view_contractor_review_queue |
| video-marketplace/MarketplaceItem | video | PerfilMarketing activo de empresa | No por el solo rol pagador | view_marketplaceitem |

Permisos `gestion_creditos.*` salvo indicacion contraria. Ser staff no basta.
PerfilPagador se evalua antes de ownership o permisos: inactivo/otra empresa no
obtiene acceso por un permiso accidental. Documentos institucionales Empresa y
BRE-B conservan sus rutas privadas existentes. Capturas sin credito conservan
ownership/permiso ID-01A. No se otorgan permisos automaticamente ni se crean nuevos.

Los videos solo se consumen en panel_list/panel_form administrativos; no se
encontraron en catalogo publico. Quedan protegidos, no en allowlist media.
Solo marketplace/items y marketplace/logos permanecen publicos.

## Storage y entrega

Confirmado por el operador en VPS:
- MEDIA_ROOT: `/var/www/aprobado/media` (realpath igual).
- PRIVATE_DOCUMENTS_ROOT: `/root/aprobado_web/private_documents`, existe, no symlink,
  realpath igual, fuera de MEDIA_ROOT y no servido por Nginx.

No se cambia upload_to, storage declarativo, nombres guardados, archivos ni DB.
No hay migracion ID-00. 0047/contractors.0019 son trabajo previo ID-01A, no ID-00.
Todas las rutas resueltas deben permanecer dentro de una de esas raices, ser
archivos existentes; se rechazan escapes por traversal o symlink fuera de raices.

`PROTECTED_LEGACY_X_ACCEL=True`: archivos MEDIA_ROOT se entregan mediante
`X-Accel-Redirect: /_protected_legacy/<nombre-codificado>` sin streaming Python.
Default False permite codigo primero/UAT local sin romper Nginx aun no configurado.
Activarlo es requisito del cierre VPS, despues de instalar el location internal.
La app sirve private_documents con FileResponse autorizado: el worker Nginx no
tiene por que atravesar /root; no abrir permisos de /root para este ticket.
Errores/desconocidos en el endpoint documental devuelven 404 no-store.

## Consumidores corregidos

Preview/listado/ZIP administrativo; comprobantes manuales; Excel/comprobantes
pagador; detalle credito; billetera; comisiones; widgets y campos readonly Django
admin; descargas Prestadores/ID-01A; videos del panel marketplace.
Los enlaces de imagen/logo publicos no cambian. Los emails ya remiten al panel;
no se encontraron serializadores con FileField publico adicionales.
PDF firmado se enlaza al archivo local protegido, no a una URL externa permanente.
Si un historico solo tiene zapsign_signed_file_url, sin PDF local, no se fabrica
un enlace publico: requiere recuperar esa evidencia mediante un proceso separado.

## ZapSign y logs

Se conserva `/api/pagares/download/<token>/` sin login como excepcion M2M:
TimestampSigner, expiracion firmada y un unico pagare/PDF original. No-store tambien
en errores. No se registra token, path ni URL en pagare_url; filtro adicional en
django.request/django.server/zapsign sanea la ruta del token en logs automaticos.
No modifica firma, estado financiero ni credenciales.

Requisito operativo: revisar access/error logs Nginx, Gunicorn, proxies, APM y CDN
antes del cierre. No registrar `$request`, `$request_uri`, Authorization, cookies,
Referer o querystrings en rutas documentales/token. No activar logs HTTP debug.
Los logs historicos requieren revision de acceso/retencion, no borrado automatico.

## Nginx propuesto: cuatro hosts, no staging

Archivo exacto: `docs/nginx/aprobado_documentos_locations.conf`.
Copiar manualmente a `/etc/nginx/snippets/aprobado_documentos_locations.conf`.
Incluir **dentro** de los server blocks HTTPS existentes de:

| server_name | Include |
|---|---|
| aprobado.com.co | include /etc/nginx/snippets/aprobado_documentos_locations.conf; |
| emprender.aprobado.com.co | include /etc/nginx/snippets/aprobado_documentos_locations.conf; |
| contratistas.aprobado.com.co | include /etc/nginx/snippets/aprobado_documentos_locations.conf; |
| market.aprobado.com.co | include /etc/nginx/snippets/aprobado_documentos_locations.conf; |

Eliminar/reemplazar el location publico anterior de /media/ en esos blocks.
No inventar/reemplazar upstreams, sockets, TLS ni includes existentes. Los blocks
HTTP deben redirigir a HTTPS o aplicar igual politica si realmente sirven contenido.
Revisar hosts alias/default_server, otros alias al mismo directorio y CDN.
No tocar fundetec-staging. No hay tarea/script que modifique Nginx.

Para evitar tokens en access logs puede definirse en contexto http un formato
sin URL ni headers, y seleccionarlo solo en esos cuatro servers:

```nginx
# http (definicion compartida, no cambia otros vhosts)
log_format aprobado_sin_urls '$request_method $status $body_bytes_sent $request_time';
# cada uno de los cuatro server blocks
access_log /var/log/nginx/aprobado_access_sin_urls.log aprobado_sin_urls;
```

Auditar locations que sobreescriban access_log. Gunicorn access log/APM requieren
equivalente saneamiento; no suponer que el filtro Django los controla. Verificar
error logs mediante fallos de prueba, pues errores de upstream pueden incluir la
URI aunque access_log este saneado. Esto es puerta UAT, no seguridad ya demostrada.

### Candidato focal 2026-11-21: minimizacion de access log por host y ruta

El VPS en `80063b1846fa89e369391c62d6e5704fb8be7404` ya tenia el bloqueo
general de `/media/`, las dos excepciones publicas y `/_protected_legacy/`
interno en los cuatro hosts. `DEBUG=False` y X-Accel activo. Las 16 sondas
anonimas, con nombres sinteticos y sin objetos reales, confirmaron 301 HTTP
y 404 no-store HTTPS para media, ruta interna y endpoint documental. Esto
no demuestra ni excluye accesos indebidos historicos. No se solicito un
documento personal. La configuracion heredaba `combined` y las cuatro
sondas del endpoint documental quedaron registradas con su query de prueba.

`b9779ed5de768ddf77bfd979e940af8dec8dbe26` no discriminaba el alias
`www.aprobado.com.co` en el redirect HTTP compartido. El candidato corregido
filtra por host literal y clase documental de la URI normalizada. Conserva
`combined` para `www` y trafico ordinario, y los `access_log off` ya vigentes
para media sensible y X-Accel. No cambia respuestas, permisos, proxies,
redirecciones, certificados ni archivos. El registro `ID00` incluye ID de
solicitud generado, metodo limitado, estado, tiempo, host y clase; no incluye
URI, args, token, IP ni headers del cliente.

El vhost real tiene un redirect HTTP compartido por cinco nombres, un HTTPS
exclusivo de `www` que redirige al apex y cuatro HTTPS de aplicacion. Hereda
`combined` y no hay override de servidor ni formatos adicionales. El plan se
contrasto en memoria con `/etc/nginx/sites-enabled/aprobado`: al retirar las
cinco lineas de include se recuperan los bytes originales; el bloque HTTPS de
`www` no cambia. Huella del origen leido, confirmable antes de instalar:
`f6fea0d371577b4d801ed09a3dcfbefe477c8289a9cad6bf4bc706b0eb299f19`.

#### Gate y procedimiento de la ventana

Listo para autorizacion de **minimizacion de access log**. El E2E autenticado
productivo permanece pendiente si no existe un archivo sintetico propio y
sesiones autorizadas. No crear datos en produccion ni usar usuarios reales
para habilitar este cambio. La disponibilidad y roles se acreditaron con
fixtures aislados; eso no se presenta como un E2E autenticado del VPS.

1. Revalidar base `80063b1846fa89e369391c62d6e5704fb8be7404` en VPS y remoto,
   inventario, candidato limpio, sin operaciones Git ni cambios concurrentes.
   Revisar `nginx -T` sin imprimir configuracion completa. Confirmar el link
   publicado al vhost, la huella indicada, ambos destinos nuevos ausentes y
   el `combined` heredado. Si cambia la topologia o se agrega un override,
   detenerse. No actualizar otro vhost.
2. Acreditar respaldo protegido y revalidar sus huellas contra los archivos
   presentes en la ventana. Referencia de preflight: `/root/backups/id00-nginx-20261121T224440Z-qp85knny/nginx-id00.tar`,
   SHA-256 `9c8b251ee4c1daf3c326e99d9f8faad79f9fdca5841bf4d01f21abdd3419c5ef`.
   Contiene vhost, enlace publicado, `nginx.conf` e include de proteccion;
   `manifest.json` registra metadatos y ausencia de los nuevos snippets.
   Archivos `0600` en directorio `0700`. Si no coincide, detenerse antes de publicar.
3. Publicar solo el SHA corregido a `release/prestadores-prod-2026-08-28`
   sin force-push. En VPS actualizar al mismo SHA por fast-forward. No hay
   migraciones, configuracion Django, collectstatic ni reinicios de la app.
4. Con el SHA exacto comprobado, copiar los dos snippets al destino aprobado
   (`/etc/nginx/conf.d/aprobado_documental_log_http.conf` y
   `/etc/nginx/snippets/aprobado_documental_log_server.conf`, `0644 root:root`),
   solo si siguen ausentes. El primer archivo lo carga el glob de `http`.
   Preparar la copia del vhost con `id00_preparar_vhost.py` como se muestra
   abajo, verificar cinco includes y metadatos; cambiar el archivo regular
   de `sites-available` sin tocar el enlace de `sites-enabled`. Ejecutar
   `nginx -t`; solo si pasa, `systemctl reload nginx` para relevo gradual de
   workers. No reiniciar otros servicios ni tocar certificados o `.env`.
5. Por host, probar HTTP `301` al mismo HTTPS, y HTTPS `404` con `no-store`
   para `HEAD` sintetico anonimo en `/media/`, `/_protected_legacy/` y
   `/documentos/` (objeto imposible `0`, sin resolver expedientes). Una query
   de prueba no sensible en `/documentos/` no debe aparecer en los access/error
   logs de esas sondas. Correlacionar por ventana, host, metodo, clase y estado
   `ID00`; una ruta ordinaria de control debe conservar `combined`. Repetir
   el control sintetico de `www`: HTTP al mismo host, HTTPS al apex, `combined`
   en ambos. Validar salud publica de Libranza/Prestadores, ausencia de nuevos
   5xx relacionados y registrar E2E autenticado productivo pendiente si no
   hay fixture autorizado. No probar con tokens reales o expedientes existentes.
6. Ante fallo, detener. Restaurar solo `sites-available/aprobado` desde el
   respaldo a una ruta temporal con huella, propietario y modo validados;
   sustituir de forma atomica y apartar solo los dos snippets nuevos. Ejecutar
   `nginx -t`. Recargar gradualmente solo si el cambio ya se habia aplicado al
   runtime. No restaurar el resto del arbol Nginx sobre cambios ajenos ni
   reescribir Git. Preservar la proteccion previa de media y los logs.

Preparacion de la copia dentro de un directorio de staging del mismo
filesystem de `/etc/nginx/sites-available`, durante la ventana autorizada:

```bash
src=/etc/nginx/sites-available/aprobado
tmp=$(mktemp -d /etc/nginx/sites-available/.id00.XXXXXX)
python3 -B docs/nginx/id00_preparar_vhost.py "$src" "$tmp/aprobado" \
  --sha256-base f6fea0d371577b4d801ed09a3dcfbefe477c8289a9cad6bf4bc706b0eb299f19
chown --reference="$src" "$tmp/aprobado"
chmod --reference="$src" "$tmp/aprobado"
```

Quedan pendientes el tratamiento de logs historicos y la evaluacion de
error logs en escenarios no cubiertos por las sondas. No reescribir, purgar
ni rotar manualmente registros. Archivos Nginx activos `0640`, `www-data:adm`,
rotacion diaria y 14 rotaciones configuradas. El piloto no acredita que los
historicos nunca contuvieran URLs privadas.

#### Validacion aislada

71/71 regresiones PostgreSQL de `test_documentos_privados` y
`test_captura_documental`; Django check limpio. Nginx 1.24.0 sin red externa
probo cuatro hosts y el alias, HTTP y HTTPS con TLS sintetico, marcadores
sinteticos en URI/queries, `combined` ordinario, denegaciones y entrega
X-Accel con misma huella y `no-store`. La simulacion del upstream prueba el
transporte Nginx; los permisos reales se prueban por separado en Django.
Se restauro la configuracion base en el contenedor y se volvieron a probar
los redirects, el acceso protegido y los logs. No se hizo reload productivo.

Reproduccion desde el worktree, con Docker, Nginx local y el venv del proyecto:

```powershell
python -B docs/nginx/tests/id00_local.py
```

## Secuencia manual de despliegue seguro (historica)

1. Revisar diff exclusivo ID-00 y dependencia ID-01A. Backup de config y evidencia
   de rutas/permissions; no copiar documentos a repositorio/logs. Validar en clon
   PostgreSQL y tomar referencias de archivos sinteticos para UAT.
2. Desplegar codigo primero con flag False; validar endpoints por rol, preview,
   Excel y token ZapSign. La exposicion /media/ sigue abierta hasta el paso 5:
   esta ventana debe ser corta y controlada, no considerar el ticket resuelto.
3. Backup protegido: `sudo cp -a /etc/nginx /root/nginx-backup-ID00-YYYYMMDDHHMM`.
   Inspeccionar config efectiva sin publicarla. Instalar solo location internal
   y saneamiento logs en los cuatro blocks, `sudo nginx -t`, recarga autorizada.
4. Activar flag True en procesos Django de validacion, UAT X-Accel/HEAD/PDF/imagenes.
   Verificar que Nginx accede /var/www/aprobado/media sin permisos adicionales /root.
5. Aplicar snippet completo (allowlist + bloqueo) en cuatro blocks, `sudo nginx -t`,
   recarga manual autorizada. No aplicar si cualquier UAT anterior falla.
6. Por host: URL directa sintetica sensible y /_protected_legacy/... deben ser 404;
   items/logos publicos 200. Propietario propio 200/ajeno 404; pagador misma empresa
   200/otra o inactivo 404; staff sin permiso 404/con permiso 200. Cerrar sesion y
   reintentar. Revisar no-store en respuestas finales Nginx (200/206/404), ausencia
   de caches publicas, PDF/image preview, Range y mobile, redireccion cross-host.
7. Token valido 200, invalido 403, expirado 410, limitado a un PDF; no aparece en
   logs de ninguna capa. Un GET humano anonimo sin token nunca obtiene documentos.
8. Rollback seguro: ante problema de X-Accel poner flag False y mantener bloqueo
   /media/; ante config invalida no recargar. Si falla codigo, mantener bloqueo
   aunque temporalmente se suspendan descargas. Restaurar una configuracion
   antigua publicamente permisiva NO es un rollback aceptable de privacidad.

Las pruebas Django no prueban ubicaciones Nginx. No se ejecutaron estas acciones.
Los URLconf conservan el helper static de MEDIA_URL solo para DEBUG; no usar
DEBUG=True en UAT de privacidad ni cargar documentos reales en desarrollo.
Caches o copias ya descargadas no pueden revocarse con estos headers; purgar CDN
si corresponde con autorizacion y revisar retencion/accesos, sin afirmar filtracion.

## Fase 2 independiente

Inventario read-only de referencias/ficheros, hashes, respaldos y permisos;
plan de copia verificada a almacenamiento privado, compatibilidad temporal,
reapuntado transaccional por lotes y rollback, despues retiro seguro del legado.
No borrar archivos ni cambiar referencias en fase 1. Ninguna reconstruccion financiera.

## Inventario de archivos ID-00

Nuevos:
- gestion_creditos/services/documentos_privados.py
- gestion_creditos/views/documentos_privados.py
- gestion_creditos/urls_documentos.py
- gestion_creditos/templatetags/documentos_privados.py
- gestion_creditos/document_widgets.py
- gestion_creditos/document_logging.py
- gestion_creditos/tests/test_documentos_privados.py
- docs/privacidad_documental_ID_00.md
- docs/nginx/aprobado_documentos_locations.conf

Integraciones modificadas (algunas ya tenian cambios ID-01A, conservados):
- aprobado_web/settings.py, aprobado_web/urls_common.py
- gestion_creditos/admin.py, gestion_creditos/forms.py
- gestion_creditos/services/pagare_url.py
- gestion_creditos/views/admin.py, gestion_creditos/views/pagador.py
- gestion_creditos/views/captura_documental.py (archivo del bloque ID-01A)
- contractors/admin.py, contractors/forms.py, contractors/views.py, contractors/views_admin.py
- templates/admin/billetera_dashboard.html, templates/Billetera/admin_billetera_dashboard.html
- templates/asesores/dashboard.html, templates/gestion_creditos/admin_asesores_dashboard.html
- templates/gestion_creditos/admin_detalle_credito.html
- templates/marketplace/panel_list.html, templates/marketplace/panel_form.html
- docs/ROADMAP_APROBADO_2026.md, docs/captura_documental_ID_01A.md

Los cambios previos de modelos/storage/migraciones/servicios de captura y sus
formularios de solicitud pertenecen a ID-01A; no se revirtieron ni se atribuyen a ID-00.

## Validacion reproducible

Usar DB de tests/clon, sin apuntar a produccion. MEDIA_ROOT y PRIVATE_DOCUMENTS_ROOT
temporales; las pruebas ID-00/ID-01A crean y limpian sus propios archivos. La
ejecucion local amplia uso un override efimero de PASSWORD_HASHERS=MD5 solo para
fixtures rapidos, sin cambiar settings de autenticacion productivos.

```bash
python manage.py test \
  gestion_creditos.tests.test_documentos_privados \
  gestion_creditos.tests.test_captura_documental \
  gestion_creditos.tests.test_admin_views \
  gestion_creditos.tests.test_pagador_dashboard \
  gestion_creditos.tests.test_pagos_offline \
  gestion_creditos.tests.test_marketplace_flow \
  gestion_creditos.tests.test_documentos_empresa \
  contractors.tests.test_portal_minimo_prestadores \
  contractors.tests.test_aprobacion_interna_prestador \
  gestion_creditos.tests.test_integridad_originacion_anulacion \
  gestion_creditos.tests.test_originacion_libranza \
  gestion_creditos.tests.test_pagos_breb --verbosity 1
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
```

No ejecutar migrate sobre la base operativa como parte de esta validacion.

Resultado local 2026-09-14 (SQLite): 340 pruebas, 333 correctas y 7 omitidas
exclusivas PostgreSQL. Incluye 24 pruebas nuevas ID-00 de permisos, ownership,
preview/traversal, X-Accel, compatibilidad legacy/privado, token, billetera,
comisiones, marketplace y render/export protegido. Check OK; makemigrations
--check --dry-run: No changes detected; git diff --check OK.
Advertencias locales no bloqueantes: GLib Windows, STATIC_ROOT inexistente en
tests y PDFs deliberadamente minimos de fixtures. PostgreSQL y Nginx no probados.
