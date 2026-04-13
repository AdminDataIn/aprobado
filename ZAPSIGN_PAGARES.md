# Integracion ZapSign - Sistema Aprobado
**Version:** 1.2 | **Fecha:** Enero 2026 | **Estado:** En pruebas sandbox

---

## Objetivo
Integrar ZapSign para firma electronica de pagares con validez legal en Colombia.

---

## Flujo funcional
1. Backend genera PDF del pagare.
2. Se envia a ZapSign y el cliente firma.
3. Webhook notifica y el credito pasa a `FIRMADO` y luego a `PENDIENTE_TRANSFERENCIA`.

---

## Marco legal (pendiente revision juridica)
- Ley 527/1999: firma electronica valida.
- Decreto 2364/2012: evidencia y trazabilidad.
- Codigo de Comercio Art. 621 y 709: requisitos del pagare.

Nota: revision juridica obligatoria del contenido del pagare antes de produccion.

---

## Arquitectura
Django -> genera PDF -> envia a ZapSign
Cliente firma -> webhook -> Django actualiza Pagare y Credito

---

## Estados
Pagare: `CREATED` -> `SENT` -> `SIGNED` (o `REFUSED`)
Credito: `APROBADO` -> `PENDIENTE_FIRMA` -> `FIRMADO` -> `PENDIENTE_TRANSFERENCIA` -> `ACTIVO`

Importante:
- El webhook NO activa el credito directamente.
- `ACTIVO` requiere confirmacion de desembolso.

---

## Componentes implementados
- `gestion_creditos/services/pagare_service.py`
  - Genera PDF con numero real de pagare.
  - Usa `credito.detalle` (emprendimiento o libranza).
  - Formatea montos COP y guarda hash SHA-256.
- `gestion_creditos/services/pagare_url.py`
  - Genera URL publica temporal para ZapSign.
- `gestion_creditos/services/zapsign_client.py`
  - Envia documento a ZapSign y guarda `token` y `sign_url`.
- `gestion_creditos/credit_services.py` -> `preparar_documento_para_firma`
  - Orquesta: crear pagare, generar URL, enviar a ZapSign.
- `gestion_creditos/views.py` -> `zapsign_webhook_view`
  - Procesa `doc_signed` y `doc_refused` con idempotencia.
  - Registra `ZapSignWebhookLog`.

---

## Configuracion (.env)
Minimo para pruebas:
```
ZAPSIGN_API_TOKEN=...
ZAPSIGN_ENVIRONMENT=sandbox   # o production
SITE_DOMAIN=aprobado.com.co
SITE_HTTPS=true
ZAPSIGN_WEBHOOK_SECRET=       # opcional
ZAPSIGN_WEBHOOK_HEADER=X-ZapSign-Secret   # opcional
```

Nota sobre webhooks:
- Webhooks creados por UI NO permiten headers custom. Dejar `ZAPSIGN_WEBHOOK_SECRET` vacio.
- Si se crean por API, se pueden enviar headers personalizados y validar autenticidad.

---

## Webhooks
URL configurada:
- `https://aprobado.com.co/api/webhooks/zapsign/`

Eventos usados:
- `doc_signed`
- `doc_refused`

Campos minimos utiles:
- `event` o `event_type`
- `token`
- `status`
- `signers` (para IP del firmante)
- `signed_file_url` / `signed_file` (URLs temporales ~60 min)

---

## Seguridad e idempotencia
- Se registra cada evento en `ZapSignWebhookLog` antes de procesar.
- Si el pagare ya esta en `SIGNED`, responde `already_processed`.
- Si `ZAPSIGN_WEBHOOK_SECRET` tiene valor, valida header definido en `ZAPSIGN_WEBHOOK_HEADER`.
- Si el header es `Authorization`, acepta formato `Bearer <secret>`.

---

## PDF publico para ZapSign
ZapSign requiere acceso publico a `url_pdf`.

Estrategia actual:
- URL firmada temporal: `/api/pagares/download/<token>/`
- Expiracion configurable por `max_age`.

Requisitos:
- Dominio publico y HTTPS en produccion.

---

## Pruebas manuales recomendadas
1. Aprobar credito y verificar que pase a `PENDIENTE_FIRMA`.
2. Confirmar que `Pagare` se crea y tiene `zapsign_sign_url`.
3. Firmar en ZapSign y verificar webhook:
   - `Pagare` -> `SIGNED`
   - `Credito` -> `PENDIENTE_TRANSFERENCIA`
4. Verificar `ZapSignWebhookLog` con payload y headers.

---

## TODO / Pendientes
- Revision juridica final del contenido del pagare.
- Descargar y almacenar el PDF firmado (`archivo_pdf_firmado`).
- Fallback job (Celery) para reconciliar estados si falla el webhook.
- Integracion completa en produccion (tokens reales, dominio, monitoreo).

---

## Documentacion oficial ZapSign
- Webhooks: https://docs.zapsign.com.br/espanol/webhooks/crear-webhook
- Crear documento: https://docs.zapsign.com.br/english/documentos/criar-documento


# SCRIP PARA MANTENIMIENTOS Y NOVEDADES:

from gestion_creditos.models import Pagare, Credito
from gestion_creditos.credit_services import preparar_documento_para_firma

credito = Credito.objects.get(numero_credito="CR-2025-00002")
pagare = credito.pagare
pagare.estado = Pagare.EstadoPagare.CREATED
pagare.zapsign_doc_token = None
pagare.zapsign_sign_url = None
pagare.zapsign_status = None
pagare.fecha_envio = None
pagare.save(update_fields=[
    'estado', 'zapsign_doc_token', 'zapsign_sign_url', 'zapsign_status', 'fecha_envio'
])

preparar_documento_para_firma(credito, credito.usuario)


## PRUEBA LOCAL DE ENVIO

Activar el true para volver a enviarlo linea 273 (Zapsign_client.py)
enviar_email_local = getattr(settings, 'ZAPSIGN_SEND_LOCAL_EMAIL', False)

ZAPSIGN_SEND_LOCAL_EMAIL=true

# FORMA RAPIDA DE RECREAR EL PAGARÉ DESDE SHELL
from gestion_creditos.credit_services import preparar_documento_para_firma
credito = Credito.objects.get(numero_credito="CR-2025-0000X") # REEMPLAZAR X POR EL NUMERO DEL CREDITO
preparar_documento_para_firma(credito, credito.usuario)