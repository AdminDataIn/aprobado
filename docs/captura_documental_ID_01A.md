# APROBADO-ID-01A: sesiones documentales

## Contrato

### Dependencia de privacidad historica ID-00

Las nuevas capturas siguen en PrivateDocumentStorage. El operador confirmo en VPS
que /root/aprobado_web/private_documents existe, no es symlink, esta separado de
/var/www/aprobado/media y no tiene alias publico Nginx. Esto actualiza la
incertidumbre de la auditoria local original anotada mas abajo.

ID-00 reutiliza autorizacion de capturas y centraliza entrega segura en
respuesta_documental, conservando nombres neutros de descarga. Los documentos
legacy no se mueven: requieren bloqueo /media/ y X-Accel segun
[procedimiento ID-00](privacidad_documental_ID_00.md). La privacidad productiva
historica queda pendiente de ese UAT; no confundirla con el storage de nuevas capturas.

`gestion_creditos.services.captura_documental` concentra ownership, contexto,
token, vigencia y validacion tecnica. No calcula credito, score ni identidad.
Productos separados: LIBRANZA y PRESTADORES; proposito fijo CEDULA.

`SesionCapturaDocumental` es el borrador previo: crearla no crea Credito.
La solicitud Libranza existente consume los archivos aceptados en su transaccion
de originacion. Prestadores vincula la sesion a ContractorApplication, incluyendo
edicion y subsanacion. Una sesion ya utilizada no admite otra vinculacion.

Estados: ABIERTA -> CANJEADA -> FINALIZADA -> UTILIZADA.
REVOCADA y EXPIRADA impiden cargas/finalizacion/vinculacion.
Regenerar una sesion abierta/canjeada invalida token, navegador y lados activos;
no extiende la expiracion. Para un nuevo periodo debe crearse otro borrador.

Token aleatorio de 32 bytes, persistido solo como SHA-256. Se entrega en el
fragmento del enlace, no en querystring. GET nunca lo consume. POST con CSRF,
usuario propietario y contexto correcto realiza canje unico y vincula la sesion
autenticada del navegador. El fragmento se retira al abrir la pagina de captura.
Si se pierde antes del canje, se requiere regeneracion/nuevo enlace. El enlace
no sustituye login. API disponible en ambos hosts bajo `/captura-documental/`.

Capturas FRONTAL/TRASERA independientes, una version activa por lado y sesion
(constraint parcial). Cada reemplazo conserva el registro y archivo previo.
Recepcion valida contenido real JPEG/PNG/WEBP, limite 8 MiB y 20 millones de
pixeles; respeta orientacion EXIF y vuelve a codificar JPEG sin metadata EXIF.
Son controles tecnicos, NO controles de calidad avanzada ni biometria.
Estado persistido: VALIDO_TECNICAMENTE / IDENTIDAD_NO_VERIFICADA.

Cada mutacion bloquea la fila principal de sesion dentro de transaction.atomic.
Finalizar es idempotente durante vigencia. Dos canjes tienen un ganador y un
rechazo controlado. Dos reemplazos se serializan. La vinculacion final conserva
el lock hasta persistir la solicitud real. Eventos registran actor, fecha y tipo,
sin token, imagen, cedula ni path. No se invocan proveedores externos.

## Storage y compatibilidad

Nuevas cedulas: `PRIVATE_DOCUMENTS_ROOT/identidad/<uuid>.jpg` (o extension admitida
en el flujo administrativo existente). No tienen URL publica. La configuracion
debe apuntar fuera de MEDIA_ROOT y de cualquier alias publico del servidor.
El storage crea directorios al escribir. No servir esta raiz con Nginx/static.

`DocumentosSolicitudStorage` lee referencias anteriores en su storage original
por compatibilidad; no las mueve ni renombra. Escribe nuevas cedulas en privado
y conserva el storage previo para documentos no identitarios. No hay data
migration ni traslado de archivos historicos. La exposicion previa de media
legacy requiere auditoria separada del servidor; este cambio no la elimina.

Descargas: borrador propietario o staff con `view_identity_documents`, nunca
PerfilPagador. Cedula del credito: propietario, staff con ese permiso o pagador
activo de la empresa (compatibilidad con revision laboral existente). Prestadores
conserva propietario y staff interno con `can_view_contractor_review_queue`.
Descarga privada sin cache y con nombre neutro; ZIP Libranza exige permiso
documental, no expone nombres originales/rutas internas.

Originacion especial administrativa conserva su entrada autorizada existente;
sus nuevas cedulas usan storage privado. No se cambia su calculo financiero.
El self-service no acepta adjuntos de identidad sin sesion. El endpoint generico
de documentos Prestadores rechaza cedula pero admite contrato/certificado.

## Limpieza

`CAPTURA_DOCUMENTAL_TTL_SECONDS`: 600 por defecto. El propietario puede revocar.
Comando manual/para programacion operativa: `python manage.py purgar_capturas_expiradas`.
Elimina solo archivos de sesiones vencidas NO utilizadas; conserva registros y
eventos con timestamp de purga. Nunca incluye sesiones utilizadas ni documentos
legacy. No se ha programado ni ejecutado sobre datos reales.
La eliminacion fisica no es transaccional: el comando admite reintento, y una
interrupcion entre filesystem y DB puede dejar pendiente el timestamp de purga.
La retencion de evidencia ya vinculada necesita politica operativa independiente.
Los FileField de las solicitudes conservan su persistencia habitual: filesystem
y SQL no forman una unica transaccion. Una originacion fallida despues de guardar
un FileField puede dejar una copia privada sin referencia; la purga de borradores
no debe borrar indiscriminadamente esas copias ni evidencia vinculada. Su revision
de retencion/orfandad debe ser explicita, no un borrado automatico de legacy.

## Migraciones y validacion

- contractors.0019_captura_documental_privada: storage del FileField documental.
- gestion_creditos.0047_captura_documental_privada: storage de cedulas Libranza,
  tres modelos documentales, constraints y permiso de lectura; depende de 0046,
  contractors.0019 y AUTH_USER_MODEL. No modifica tablas financieras existentes
  salvo el estado Django del storage de esos FileField.

Pruebas principales: gestion_creditos.tests.test_captura_documental.
Regresiones: portal Prestadores, revision/subsanacion, originacion Libranza,
integridad/anulacion, admin, pagador y originacion especial.
CapturaConcurrenciaPostgresTest se omite expresamente en SQLite; ejecutar en
clon PostgreSQL con las migraciones nuevas, nunca contra produccion.

Validacion local del 12/09/2026:
- Suite ampliada: 211 tests, 205 OK y 6 skips PostgreSQL (incluye 3 de captura).
- Suite documental final, incluyendo ID de solicitud 1234: 22 tests, 19 OK y 3 skips.
- check, makemigrations --check --dry-run, git diff --check y node --check: OK.
- La suite ampliada uso MEDIA_ROOT/PRIVATE_DOCUMENTS_ROOT temporales y hasher MD5
  exclusivamente en memoria del proceso de tests para reducir el costo de fixtures.
  La suite documental final se ejecuto con manage.py test y configuracion normal.
- Raices locales verificadas: private_documents y media como directorios hermanos
  de BASE_DIR; private_documents esta ignorado por Git. Nginx real no se inspecciono.
- Migraciones aplicadas solo a bases vacias creadas por tests, no a db.sqlite3.

## Archivos del bloque

Modelos/storage/admin:
`gestion_creditos/models.py`, `gestion_creditos/storage.py`,
`gestion_creditos/admin.py`, `contractors/models.py`, `contractors/admin.py`.

Servicio y rutas comunes:
`gestion_creditos/services/captura_documental.py`,
`gestion_creditos/views/captura_documental.py`, `gestion_creditos/urls_captura.py`,
`aprobado_web/urls_common.py`,
`gestion_creditos/management/commands/purgar_capturas_expiradas.py`.

Integraciones y descargas:
`gestion_creditos/views/solicitudes.py`, `gestion_creditos/views/admin.py`,
`gestion_creditos/views/pagador.py`, `contractors/views.py`,
`contractors/views_admin.py`, `contractors/forms.py`,
`contractors/services/solicitud.py`, `contractors/services/subsanacion.py`.

Templates y assets:
`templates/gestion_creditos/solicitud_libranza.html`,
`templates/gestion_creditos/captura_continuacion.html`,
`templates/gestion_creditos/components/captura_handoff.html`,
`templates/contractors/solicitud_prestador.html`,
`templates/contractors/atender_subsanacion_prestador.html`,
`static/css/captura_documental.css`, `static/js/captura_documental.js`.

Tests:
`gestion_creditos/tests/test_captura_documental.py`,
`gestion_creditos/tests/captura_fixtures.py`,
`gestion_creditos/tests/test_integridad_originacion_anulacion.py`,
`gestion_creditos/tests/test_originacion_libranza.py`,
`contractors/tests/test_portal_minimo_prestadores.py`.
Ademas, las dos migraciones antes indicadas y este documento.

## Limites e ID-01B

El selector movil (pointer/viewport/capture) es UX, no prueba del dispositivo
fisico. No se confia en source=camera, User-Agent ni declaraciones del cliente.
ID-01A prepara enlace local compatible con un futuro QR; no agrega dependencia
QR ni servicio externo. ID-01B debe cerrar UX de camara, previsualizacion, toma
y repeticion accesibles, pruebas en dispositivos reales y eventual QR local.
OCR, biometria, liveness y validacion de identidad no estan implementados.
