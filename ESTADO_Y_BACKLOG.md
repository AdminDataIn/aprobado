# Estado y Backlog

Backlog vivo y estado real del proyecto. Este archivo debe decir la verdad actual del código, no la deseada.

**Fecha de corte:** 2026-04-10

## Resumen ejecutivo

El proyecto está funcional en sus productos principales, pero no está completamente limpio a nivel interno. La plataforma ya soporta libranza, emprendimiento, pagador, marketplace e inversionista, pero el mayor foco de deuda técnica sigue concentrado en `gestion_creditos`.

La situación real hoy es:

- el producto funciona
- los flujos más sensibles ya tienen base operativa
- la arquitectura interna todavía convive con módulos legacy muy grandes

## Qué está sólido

### Core y operación

- Separación por host/subdominio en `main`, `emprender` y `market`.
- Libranza con originación, activación, dashboard, pagos y trazabilidad relevante.
- Pagador con carga de empleados, pago offline, carga por Excel y selección directa de obligaciones.
- Marketplace general, por empresa y panel admin funcionando con auth corregido.
- Inversionista con auth separado y dashboard base.

### Flujos sensibles

- Cierre formal de crédito a `PAGADO` sin dejar saldos ni cuotas pendientes.
- Resumen unificado de pagos desde amortización para créditos especiales.
- Correos diferenciados entre contacto visible y remitente SMTP.
- Notificación de carga de pagos offline al pagador.
- Política nueva de notificaciones de cuotas pendientes con resumen mensual al pagador y aviso posterior al usuario.

### Calidad mínima validada

- `manage.py check` en verde.
- Suite dirigida en verde:
  - `gestion_creditos.test_credit_lifecycle`
  - `gestion_creditos.test_name_normalization`
  - `gestion_creditos.test_pagos_offline`
  - `gestion_creditos.tests_marketplace_flow`
  - `usuarios.tests`

## Qué quedó parcial

### Refactorización de `gestion_creditos`

Quedó avanzada, no cerrada.

Sí existe:

- wrappers por dominio:
  - `views_admin.py`
  - `views_pagador.py`
  - `views_marketplace.py`
- servicios nuevos:
  - `dashboard_metrics.py`
  - `credit_lifecycle.py`
  - `payments.py`
  - `name_normalization.py`
  - `interest_proration.py`

Pero siguen siendo fuentes reales de verdad:

- `gestion_creditos/views.py`
- `gestion_creditos/credit_services.py`
- `gestion_creditos/tasks.py`

### Intereses prorrateados

Solo quedó la base técnica para implementarlo con seguridad. No está activo en producción ni reemplaza el cálculo actual.

### Mercado Pago

Estado real:

- hay diagnóstico y decisión técnica previa
- no existe integración productiva cerrada

Falta:

- creación de preferencia
- `init_point`
- webhook idempotente
- conciliación real

No debe presentarse como listo.

## Pendientes prioritarios

### 1. Cerrar de verdad la refactorización de `gestion_creditos`

Necesario antes de seguir creciendo sobre admin/pagador/marketplace.

Pendiente:

- extraer lógica real de `views.py`
- extraer más responsabilidades de `credit_services.py`
- pasar tests a `gestion_creditos/tests/`
- dejar contratos de servicio más claros

### 2. Limpiar documentación especializada y legacy

El frente raíz ya quedó ordenado, pero `docs/` todavía mezcla documentos vigentes con documentos históricos.

### 3. Terminar la política nueva de notificaciones desde una validación operacional

El código ya existe, pero falta cierre operativo en servidor:

- beat schedules
- entorno de Celery
- verificación de destinatarios reales

### 4. Ejecutar normalización histórica de nombres

La regla nueva ya existe para altas nuevas, pero el histórico requiere corrida controlada con:

- `normalizar_nombres_mayuscula --apply`

### 5. Consolidar el dashboard del inversionista con datos reales

Hoy existe estructura útil, pero sigue mezclando demo/base y necesita consolidarse con datos reales o un seed QA estable.

## Pendientes secundarios

- limpieza de encoding residual en algunos archivos viejos
- reducción de rutas legacy si se confirma que ya no se usan
- cobertura adicional del flujo de selección directa de obligaciones
- limpieza del scheduling legacy en `tasks.py`/`celery.py`

## Lo que ya no debe aparecer como pendiente principal

- cambio de CSV a Excel como formato oficial del pagador
- carga masiva offline con comprobante
- resumen al pagador de carga de pagos
- auth cerrado de pagador e inversionista
- corrección del marketplace admin reset
- dashboard admin con filtro por empresa
- cierre formal a `PAGADO`

## Riesgos si se despliega sin más limpieza

- bajo riesgo funcional en los flujos cubiertos por tests dirigidos
- riesgo medio de mantenimiento por seguir dependiendo de `views.py` y `credit_services.py`
- riesgo medio de confusión operativa si alguien usa documentación vieja como fuente de verdad
- riesgo alto solo si alguien interpreta Mercado Pago o prorrateo de intereses como ya cerrados productivamente

## Recomendación de orden

1. mantener deploys pequeños y validados
2. cerrar refactorización interna de `gestion_creditos`
3. consolidar documentación especializada
4. luego retomar frentes de producto como inversionista o pasarelas nuevas
