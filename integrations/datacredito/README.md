# Integracion DataCredito real

Este paquete contiene la capa tecnica base para integrar DataCredito/Experian
sin ejecutar consumos productivos por defecto.

## Estado

- No crea creditos.
- No origina solicitudes.
- No toca pagos.
- No toca score productivo.
- No guarda respuestas crudas.
- No imprime secretos.
- Todo consumo real queda bloqueado si `DATACREDITO_REAL_ENABLED=False`.

## Variables de entorno

```env
DATACREDITO_REAL_ENABLED=False
DATACREDITO_ENVIRONMENT=uat
DATACREDITO_TIMEOUT_SECONDS=15
DATACREDITO_DOCUMENT_HASH_SECRET=
DATACREDITO_REUSE_DAYS=30

DATACREDITO_DECISOR_CLIENT_ID=
DATACREDITO_DECISOR_CLIENT_SECRET=
DATACREDITO_DECISOR_USERNAME=
DATACREDITO_DECISOR_PASSWORD=

DATACREDITO_HDC_CLIENT_ID=
DATACREDITO_HDC_CLIENT_SECRET=
DATACREDITO_HDC_USERNAME=
DATACREDITO_HDC_PASSWORD=

DATACREDITO_HDC_SERVICE_USER=
DATACREDITO_HDC_SERVICE_PASSWORD=
DATACREDITO_HDC_PRODUCT_ID=64
DATACREDITO_HDC_INFO_ACCOUNT_TYPE=1
DATACREDITO_HDC_SERVER_IP_ADDRESS=
DATACREDITO_HDC_CHANNEL_NAME=Canal-01
DATACREDITO_HDC_CHANNEL_TYPE=42
```

Opcionales para ajustes de conectividad:

```env
DATACREDITO_TOKEN_URL=
DATACREDITO_REVOKE_TOKEN_URL=
DATACREDITO_MIDECISOR_URL=
DATACREDITO_HISTORIAL_URL=
```

## Endpoints base

Token:

- UAT: `https://uat-api.datacredito.com.co/spla/oauth2/v1/token`
- Produccion: `https://api.datacredito.com.co/spla/oauth2/v1/token`

Revocar token:

- UAT: `https://uat-api.datacredito.com.co/spla/oauth2/v1/revokeToken`
- Produccion: `https://api.datacredito.com.co/spla/oauth2/v1/revokeToken`

MiDecisor:

- UAT: `https://uat-api.datacredito.com.co/co/cs/midecisor/v1/client`
- Produccion: `https://prod-api.datacredito.com.co/co/cs/midecisor/v1/client`

Historia de Credito:

- UAT: `https://uat-api.datacredito.com.co/cs/credit-history/v1/hdcplus`
- Produccion: `https://api.datacredito.com.co/cs/credit-history/v1/hdcplus`

## Perfiles De Credenciales

Las colecciones oficiales de Demo/UAT separan credenciales por servicio:

- `decisor`: OAuth exclusivo para MiDecisor.
- `historial`: OAuth exclusivo para Historia de Credito HPN.

Ambos generan token en:

- `POST https://uat-api.datacredito.com.co/spla/oauth2/v1/token`

El request OAuth usa headers:

- `client_id`
- `client_secret`
- `Content-Type: application/json`

Y body:

```json
{
  "username": "...",
  "password": "..."
}
```

No hay fallback silencioso entre perfiles nuevos. Las variables legacy
`DATACREDITO_CLIENT_ID`, `DATACREDITO_CLIENT_SECRET`, `DATACREDITO_USERNAME`,
`DATACREDITO_PASSWORD`, `DATACREDITO_API_PASSWORD`, `DATACREDITO_PRODUCT_ID` y
`DATACREDITO_SERVER_IP_ADDRESS` solo se conservan como compatibilidad temporal y
emiten advertencia tecnica cuando se usan para OAuth. Deben eliminarse cuando se
complete la rotacion de credenciales.

Si alguna credencial fue compartida en colecciones, chats o capturas, debe
rotarse antes de habilitar consumo real.

## Historia De Credito HPN

HPN usa token del perfil `historial`, pero tambien exige credenciales internas
en el body:

- `DATACREDITO_HDC_SERVICE_USER`
- `DATACREDITO_HDC_SERVICE_PASSWORD`

Ademas exige headers de producto/canal:

- `serverIpAddress`
- `ProductId`
- `InfoAccountType`
- `client_id`
- `client_secret`
- `Authorization: Bearer <token>`

Por cada consulta se genera un `requestUUID` nuevo y una fecha timezone-aware.
No se reutilizan UUID de Postman, no se hardcodea IP y no se loguea el body
completo.

## Seguridad

El token OAuth2 se cachea con Django cache usando `expires_in`.
El `access_token`, `client_secret`, passwords y API keys nunca deben registrarse
completos en logs ni incluirse en resultados normalizados.

Los clientes retornan estructuras seguras y sanitizadas. Los normalizadores
extraen score, estado de mora y metadata minima, pero no conservan JSON/XML
completo del proveedor.

## Normalizacion Contract-Driven

La normalizacion se basa en las fuentes locales:

- `DocsIntegracionDatacredito/MiDecisor/Servicio/DecisorServicio.pdf`
- `DocsIntegracionDatacredito/MiDecisor/Servicio/Swagger MiDecisor.yaml`
- `DocsIntegracionDatacredito/MiDecisor/Servicio/Ejemplo Salida MiDecisor PN.json`
- `DocsIntegracionDatacredito/HistorialCredito/HistorialCreditoServicio.pdf`
- `DocsIntegracionDatacredito/HistorialCredito/HPN_REST_EJEMPLO.postman_collection.json`

MiDecisor PN se lee desde rutas contractuales:

- `content.infoTransaccion.codigosRespuesta`
- `content.respuesta.validacion.conInformacion`
- `content.respuesta.comportamientoCrediticio`
- `content.respuesta.informacionRiesgo`
- `content.respuesta.endeudamiento`

El score principal de MiDecisor es:

- `content.respuesta.informacionRiesgo.score`

Se conserva como `score_midecisor` y `fuente_score=MIDECISOR`. No se reemplaza
automaticamente por modelos de Historia de Credito.

Historia de Credito HPN se lee desde:

- `ReportHDCplus.productResult.responseCode`
- `ReportHDCplus.productResult.responseDesc`

Mapeo minimo de codigos HDC:

- `02`: `ERROR_CREDENCIAL_SERVICIO`
- `09`: `IDENTIFICACION_NO_ENCONTRADA`
- `10`: `APELLIDO_NO_COINCIDE`
- `12`: `CONFIGURACION_BLOQUEADA`
- `13`: `EXITOSA_CON_INFORMACION`
- `14`: `EXITOSA_SIN_INFORMACION`
- `17`: `CONFIGURACION_VENCIDA`
- `23`: `ERROR_TEMPORAL`

Los modelos de score de HDC se conservan en `scores_hdc` con
`fuente=HISTORIA_CREDITO`. No se promedian, no se mezclan con MiDecisor y no se
usan como score principal hasta validar una respuesta Demo real codigo `13` y
confirmar el modelo contratado.

Valores como `"-"`, `"-1"`, cadenas vacias, `null` o campos ausentes se
normalizan a `None`. Ausencia de mora no se convierte en cero; `mora_severa`
queda `None` salvo evidencia explicita en vector, saldo o campos contractuales.

Las alertas MiDecisor por coincidencia solo por nombre se tratan como revision
de cumplimiento:

- `requiere_revision_cumplimiento=True`
- `bloqueo_automatico=False`

No se almacena ni expone el texto completo de alertas sensibles en metadata
publica; solo cantidad y tipos resumidos.

## Diagnostico UAT manual

Existe un comando manual para probar conectividad UAT sin guardar respuestas
crudas ni crear registros financieros:

```bash
python manage.py diagnosticar_datacredito_uat \
  --tipo-documento CC \
  --numero-documento 123456789 \
  --apellido KENT \
  --servicio decisor \
  --confirmar-consumo-real
```

Para salida JSON sanitizada:

```bash
python manage.py diagnosticar_datacredito_uat \
  --tipo-documento CC \
  --numero-documento 123456789 \
  --apellido KENT \
  --servicio ambos \
  --confirmar-consumo-real \
  --json
```

Para validar configuracion sin consumo real:

```bash
python manage.py diagnosticar_datacredito_uat --validar-configuracion
```

La validacion muestra solo si los perfiles estan completos, endpoints y campos
no sensibles como `ProductId`, `InfoAccountType` y estado de `serverIpAddress`.
No imprime secretos ni ejecuta proveedores.

Para diagnosticar usando snapshots persistidos y evitar consumos repetidos:

```bash
python manage.py diagnosticar_datacredito_uat \
  --tipo-documento CC \
  --numero-documento 123456789 \
  --apellido KENT \
  --servicio decisor \
  --confirmar-consumo-real \
  --usar-snapshot \
  --json
```

`--forzar-consulta` ignora un snapshot vigente y crea uno nuevo si la respuesta
es funcionalmente reutilizable. Solo se acepta junto con
`--confirmar-consumo-real`.

Guardas del comando:

- No ejecuta consumo real sin `--confirmar-consumo-real`.
- No ejecuta consumo real si `DATACREDITO_REAL_ENABLED=False`.
- Aborta si `DATACREDITO_ENVIRONMENT=prod`.
- No imprime token, secretos, password ni documento completo.
- No guarda en base de datos.
- Solo muestra resumen sanitizado: servicio, `http_status`, codigo funcional,
  estado normalizado, informacion disponible, score/mora disponibles y flags de
  revision manual/cumplimiento.

## Snapshots Seguros Y Reutilizacion

La capa de snapshots diferencia tres conceptos:

- Consulta tecnica: request al proveedor y normalizacion en memoria.
- Snapshot persistido: copia segura y resumida de una respuesta funcional.
- Informacion utilizable para score: solo existe cuando el resultado trae score
  disponible y una respuesta funcional con informacion suficiente.

El modelo `ConsultaDatacreditoSnapshot` guarda un snapshot sanitizado por
servicio (`decisor` o `historial`) y ambiente. No guarda documento completo,
apellido, token, credenciales, raw response, correo, telefono, direccion ni
datos personales completos.

La reutilizacion inicial dura `DATACREDITO_REUSE_DAYS`, por defecto `30` dias.
Al vencer, el snapshot historico queda disponible para auditoria, pero no se
usa para evitar una nueva consulta.

El identificador de consulta usa HMAC:

```text
ambiente + servicio + tipo_documento + documento + apellido_normalizado
```

El secreto viene de `DATACREDITO_DOCUMENT_HASH_SECRET`. En produccion no hay
fallback silencioso: si falta este secreto, el servicio bloquea la persistencia
con error de configuracion. El apellido hace parte del fingerprint porque una
consulta con apellido incorrecto no debe bloquear una consulta corregida.

Solo se reutilizan estados funcionales procesados:

- `EXITOSA_CON_INFORMACION`
- `EXITOSA_SIN_INFORMACION`
- `IDENTIFICACION_NO_ENCONTRADA`
- `APELLIDO_NO_COINCIDE` cuando coincide el mismo fingerprint completo

No se persisten ni reutilizan como resultado valido:

- errores de credencial o configuracion;
- configuracion bloqueada o vencida;
- errores temporales o tecnicos;
- HTTP 401 o HTTP 5xx;
- errores de parseo JSON;
- errores de normalizacion;
- errores del adapter.

El snapshot guarda solamente campos normalizados seguros como estado, score
MiDecisor, modelos HDC resumidos, saldos agregados, cuota total, cantidad de
creditos, porcentaje de deuda, ingreso estimado, mora, viabilidad, rating,
monto sugerido, cantidad de alertas y flags de revision. Las alertas quedan
solo como resumen sanitizado.

Para evitar consumos duplicados concurrentes se usa un lock temporal en cache:

```text
datacredito:consulta:<ambiente>:<servicio>:<fingerprint>
```

Si otro proceso esta consultando el mismo fingerprint, el segundo espera
brevemente e intenta reutilizar el snapshot creado por el primero. No se
mantiene una transaccion de base de datos abierta durante la llamada HTTP.

El permiso futuro `integrations.can_force_datacredito_refresh` permite modelar
una accion staff de consulta forzada. En esta fase no se conecta a una vista.

El provider real sigue sin conectarse automaticamente al formulario publico ni a
originacion. `CONTRACTORS_DATACREDITO_PROVIDER=mock` permanece como valor por
defecto.

## Pendiente

- Validar respuesta Demo real HDC codigo `13` con el modelo contratado.
- Definir trazabilidad legal de autorizacion previa del titular.
