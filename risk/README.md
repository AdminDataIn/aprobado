# Risk

## Proposito

Dominio responsable de decisiones de riesgo reutilizables por originacion,
libranza, credito personas y futuras APIs internas.

## Servicios actuales

## Escenarios compartidos de libranza

Los valores de escenario (`NUEVO_CREDITO`, `SEGUNDO_CREDITO` y
`RECOGIDA_CARTERA`) viven en `libranza.escenarios_credito`, no en `risk`.
`risk` implementa las evaluaciones reutilizables de segundo credito y recogida
de cartera, pero no es propietario del catalogo de escenarios del producto.

### SecondCreditService

Evalua si un cliente puede mantener su credito actual y tomar un credito
adicional. No mezcla reglas de recogida de cartera.

Reglas implementadas:

- bloquea mora activa relevante
- valida minimo 40% pagado sobre el credito vigente
- calcula cuota actual, cuota proyectada y porcentaje comprometido
- bloquea si supera capacidad maxima configurable
- valida maximo configurable de creditos activos simultaneos

Salida:

- `eligible`
- `reason`
- `paid_percentage`
- `required_percentage`
- `blocking_credit_id`
- `current_installment`
- `projected_installment`
- `committed_percentage`
- `maximum_capacity`
- `residual_capacity`
- `current_active_credits`

### ServicioRecogidaCartera

Evalua si un nuevo credito puede recoger cartera vigente. Es un escenario
distinto a segundo credito: el saldo vigente se descuenta del nuevo monto y se
calcula desembolso neto.

Reglas implementadas:

- minimo 40% pagado del credito actual
- bloquea mora activa relevante
- calcula porcentaje pagado
- calcula saldo pendiente
- calcula valor a recoger
- calcula desembolso neto
- rechaza si el monto solicitado no supera el saldo a recoger

Salida:

- `applies`
- `eligible`
- `reason`
- `current_credit_id`
- `outstanding_balance`
- `takeover_amount`
- `net_disbursement_amount`
- `paid_percentage`
- `required_percentage`
- `requested_amount`

## Policies

Las reglas reutilizables viven en `risk.policies`:

- `porcentaje_pagado`: porcentaje pagado y saldo pendiente normalizado
- `mora`: deteccion de mora activa relevante
- `capacidad`: capacidad maxima, porcentaje comprometido y residual
- `elegibilidad`: minimo pagado y maximo de creditos activos

## Selectors

`risk.selectors` contiene queries ORM read-only necesarias para politicas de
riesgo. No debe hacer writes ni tomar decisiones de negocio.

## Supuestos actuales

- La mora relevante se toma desde creditos en estado `EN_MORA` con saldo
  pendiente positivo.
- El porcentaje pagado usa `monto_aprobado` y `capital_pendiente`.
- El saldo a recoger usa `saldo_pendiente` y cae a `capital_pendiente` si falta
  saldo.
- La capacidad maxima usa `RISK_MAX_DEBT_BURDEN_PERCENTAGE` si existe; por
  defecto aplica 50%.
- El maximo de creditos activos simultaneos usa
  `RISK_MAX_SIMULTANEOUS_ACTIVE_CREDITS` si existe; por defecto aplica 2.
- Los servicios son read-only: no crean solicitudes, no modifican creditos y no
  disparan tareas.

## Extension futura

- Score interno y externo como policies adicionales.
- DataCredito como adaptador externo, no dentro de estos servicios puros.
- Politicas por pagaduria, convenio, segmento o producto.
- Versionado explicito de politicas para auditoria de decisiones.
- Integracion posterior con originacion, dashboards, WhatsApp y workflows.

## No conectado todavia

Estos servicios aun no estan conectados a:

- endpoints
- originacion
- WhatsApp
- dashboards
- DataCredito
- workflows productivos

## Regla de oro

Risk decide. Originacion ejecuta. WhatsApp solo orquesta.
