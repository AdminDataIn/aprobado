# P0-5B2.2B2: consentimiento y readiness local

Implementado para validacion local; no constituye UAT de proveedores ni cierre E2E.
No se modifican .env, credenciales reales, flags, score, politica ni datos operativos.
La confirmacion operativa previa indica prestadores-score-prod-v1 persistida INACTIVA.

## Consentimiento canonico

`contractors/consentimiento_centrales.py` conserva literalmente el contenido de
`legal_prestadores_view`, seccion centrales: titulo, actualizacion Julio de 2026,
introduccion, seis clausulas y destacado. No se redactaron clausulas nuevas.
La igualdad del diccionario original/trasladado se contrasto mediante AST.
El SHA256 del diccionario serializado UTF-8, claves ordenadas, es
`f40d1f8b3bcf5397874b34528070e7f849790e61aef21ed6e81f8b5ea61f90e3`.
Este hash de procedencia NO es el hash de aceptacion del texto.

La version inicial es `prestadores-centrales-v1`. Desde P0-5B2.2B2.1 el texto y version
efectivos SIEMPRE provienen de este modulo versionado. Settings expone esos defaults;
variables juridicas presentes solo pueden confirmar igualdad EXACTA, sin strip ni
normalizacion. Vacias, diferentes o con espacios/saltos adicionales bloquean UI,
aceptacion, reutilizacion y readiness. Nunca sustituyen silenciosamente el cuerpo.
Ese mismo texto se renderiza escapado, preservando saltos, en el formulario y pagina
legal. Su SHA256 UTF-8 se persiste como evidencia.
El resumen del checkbox es un CTA, no sustituye al cuerpo legal hasheado.

Call graph: consentimiento_centrales.py -> settings (compatibilidad) ->
obtener_configuracion_autorizacion_datacredito -> formulario/vista legal -> template
-> POST/CSRF/confirmacion firmada -> servicio -> version y SHA256 persistidos.
Readiness consume ese mismo objeto, valida cuerpo/version/SHA256 canonicos y ambas
coincidencias. No imprime el texto ni el valor de overrides distintos.
Una version futura requiere cambio/revision de codigo y nueva aceptacion; variables
antiguas incompatibles bloquean hasta que un operador las elimine o las alinee.
No se hace backfill ni se modifican/reinterpretan hashes de evidencia historica.

Solicitud nueva: checkbox desmarcado, POST afirmativo y CSRF. La confirmacion firmada
del formulario vincula actor/version/hash mostrados; un cambio entre GET y POST
exige recargar y aceptar el texto vigente. No es una credencial de DataCredito.
Se conserva la evidencia existente inmutable con usuario, solicitud, fecha, hash,
version, IP hasheada y user-agent acotado. No se agrega caducidad del consentimiento.

Solicitud historica: no hay backfill ni inferencia desde el booleano antiguo.
Mi credito ofrece aceptar la autorizacion sobre la solicitud existente mediante
`solicitud/<id>/consentimiento-centrales/`, solo para su titular autenticado.
GET no escribe; POST crea/reutiliza evidencia bajo bloqueo de solicitud.
Version/hash/titular diferentes invalidan compatibilidad; el historico permanece.
No se crea Credito ni se ejecutan proveedores, score o pagos. El control existente
de version de datos invalida evaluacion previa solamente si no hay credito originado
ni etapa de firma. La nueva aceptacion preserva estados financieros y de firma.

## Comando y servicio de solo lectura

```powershell
python manage.py verificar_readiness_datacredito
python manage.py verificar_readiness_datacredito --json
```

`integrations.datacredito.readiness.evaluar_readiness_datacredito()` no consulta
DB, OAuth, HTTP o DNS. No descubre IP publica ni prueba credenciales. Cada requisito
expone nombre/configurado/critico/mensaje/estado, nunca sus valores secretos.
URLs no contractuales se rechazan sin mostrarlas (podrian contener secretos).

Estados: CONFIGURADO_PERO_DESHABILITADO, INCOMPLETO_Y_DESHABILITADO,
CONFIGURADO_Y_HABILITADO e INCOMPLETO_Y_HABILITADO. Resultado LISTO_CONFIGURACION
solo si todos los requisitos criticos cumplen. Flags False no son credenciales
faltantes y el comando nunca los modifica. LISTO_CONFIGURACION no autoriza consumo.

## Configuracion requerida sin valores secretos

GLOBAL / CONSENTIMIENTO:
- DATACREDITO_ENVIRONMENT (uat para el DEMO contratado de Aprobado).
- DATACREDITO_DOCUMENT_HASH_SECRET.
- DATACREDITO_AUTHORIZATION_TEXT / DATACREDITO_AUTHORIZATION_TEXT_VERSION:
  opcionales; solo se admiten si coinciden exactamente con la definicion en codigo.
- DATACREDITO_TOKEN_URL / DATACREDITO_REVOKE_TOKEN_URL (defaults confirmados).
- DATACREDITO_ENABLED=False / DATACREDITO_REAL_ENABLED=False.

MIDECISOR:
- DATACREDITO_DECISOR_CLIENT_ID / DATACREDITO_DECISOR_CLIENT_SECRET.
- DATACREDITO_DECISOR_TOKEN_USERNAME / DATACREDITO_DECISOR_TOKEN_PASSWORD.
- DATACREDITO_MIDECISOR_URL.

HDC:
- DATACREDITO_HDC_CLIENT_ID / DATACREDITO_HDC_CLIENT_SECRET.
- DATACREDITO_HDC_TOKEN_USERNAME / DATACREDITO_HDC_TOKEN_PASSWORD.
- DATACREDITO_HDC_SERVICE_USER / DATACREDITO_HDC_SERVICE_PASSWORD.
- DATACREDITO_HDC_SERVER_IP_ADDRESS / DATACREDITO_HISTORIAL_URL.
- DATACREDITO_HDC_PRODUCT_ID / DATACREDITO_HDC_INFO_ACCOUNT_TYPE.
- DATACREDITO_HDC_CHANNEL_NAME=CONEXRED-01 / DATACREDITO_HDC_CHANNEL_TYPE=42.
- DATACREDITO_HDC_PARAMETERS_JSON, si lo requiere el contrato; JSON invalido bloquea.

Se exigen credenciales canonicas separadas por servicio, no basta un fallback legacy.
OAuth token/revoke siguen siendo URLs compartidas; no se separan en este ticket.
Servidor HDC: solo presencia local; el consumo debe egresar por la IP autorizada
por Experian. Esa verificacion operativa no puede deducirse de readiness local.

DEMO contratado utiliza los endpoints UAT confirmados para Aprobado:
- Token: https://uat-api.datacredito.com.co/spla/oauth2/v1/token
- Revoke: https://uat-api.datacredito.com.co/spla/oauth2/v1/revokeToken
- MiDecisor: https://uat-api.datacredito.com.co/co/cs/midecisor/v1/client
- HDC: https://uat-api.datacredito.com.co/cs/credit-history/v1/hdcplus

No se cambiaron defaults de endpoints. El canal por defecto anterior Canal-01
se mantiene por compatibilidad, pero NO satisface el contrato CONEXRED-01.
Los defaults ProductId=64 / InfoAccountType=1 NO demuestran confirmacion contractual:
sin declaracion explicita en entorno se reporta DEFAULT_NO_CONFIRMADO /
PENDIENTE_CONFIRMACION y NO_LISTO. Valores explicitos positivos se informan como
CONFIGURADO_EXPLICITAMENTE; el operador debe suministrarlos solo con respaldo de
Experian. El codigo no verifica autenticidad de dicho respaldo ni valida producto.

## HMAC

Se exige minimo 32 bytes UTF-8 no vacios, tambien antes del consumo runtime.
La longitud no prueba entropia: Aprobado puede generarlo internamente con
`secrets.token_urlsafe(32)`, fuera de logs/Git y con manejo seguro. No se genera
ningun valor en este ticket. No es una credencial entregada por Experian.
HMAC-SHA256 protege documento y componentes de fingerprint (cuenta/apellido).
Rotarlo cambia hashes/fingerprints y deja de reutilizar snapshots previos;
los historicos NO se reescriben ni se reinterpretan con la nueva clave.

## Validacion

Pruebas de consentimiento: render completo/hash, version, CSRF real, titular,
configuracion faltante, checkbox ausente, POST obsoleto/manipulado, reaceptacion
historica sin nueva solicitud, idempotencia, ownership revalidado bajo lock y
conservacion del estado de solicitudes originadas/en firma.
Readiness: defaults, canal, componentes faltantes, HMAC corto, salida sin secretos,
flags informativos, cero consultas DB y HTTP/DNS bloqueados con mocks que fallan.
Regresion: contractors.tests e integrations.tests, proveedores simulados solamente.

En VPS/clon PostgreSQL ejecutar las mismas suites sin cargar credenciales/activar
flags y la nueva ConsentimientoCentralesConcurrenciaPostgresTest: doble aceptacion
debe producir una evidencia y el mismo timestamp. SQLite la omite explicitamente.
Resultados locales de B2.2B2, anteriores al contrato estricto B2.2B2.1
(SQLite, caché de pruebas en memoria y HTTP/DNS bloqueados):
- Focal: consentimiento, autorizacion v2, readiness y enlace legal del portal:
  34 tests en 6.245 s, OK, una omision PostgreSQL.
- Amplia: contractors.tests + integrations.tests:
  530 tests en 544.966 s, OK, 13 omisiones PostgreSQL.
- manage.py check: sin incidencias.
- makemigrations --check --dry-run: No changes detected.
- git diff --check: limpio; nuevos archivos revisados sin errores de whitespace.
Estos resultados no validan conectividad de proveedores ni locks PostgreSQL.

Validacion final B2.2B2.1 (SQLite, cache en memoria, HTTP/DNS bloqueados):
- Consentimiento, autorizacion v2, readiness y snapshots v2:
  54 tests en 18.723 s, OK, una omision PostgreSQL.
- contractors.tests + integrations.tests:
  536 tests en 541.601 s, OK, 13 omisiones PostgreSQL.
- check sin incidencias; makemigrations --check --dry-run sin cambios;
  git diff --check limpio. Regresion PostgreSQL aun requerida en VPS/clon.

Diagnostico de configuracion local efectiva (no consulta de produccion): NO_LISTO /
INCOMPLETO_Y_HABILITADO; ambos flags ya estaban True, no se modificaron. Las suites
se ejecutan con flags False y HTTP/DNS bloqueados. La confirmacion previa de flags
apagados corresponde a produccion; no se verifico ni modifico ese entorno.
Faltan DATACREDITO_DECISOR_TOKEN_USERNAME/PASSWORD y
el canal HDC difiere de CONEXRED-01. Existen overrides locales preexistentes de
texto/version distintos del cuerpo canonico inicial; no se editaron ni mostraron
valores. B2.2B2.1 los rechaza con NO_LISTO: no se presentan ni se aceptan como cuerpo
legal, tampoco alteran la evidencia previa. El operador debe eliminarlos o alinearlos
exactamente, mediante una operacion aparte autorizada. Este diagnostico LOCAL no
demuestra el estado de produccion. No se cargaron secretos nuevos en el ticket.
