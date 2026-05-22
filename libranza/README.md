# Libranza

## Proposito

Dominio responsable de reglas propias del producto libranza: ley de libranza,
pagador/convenio, vinculo laboral, capacidad de descuento y reglas de nomina.

## Servicios actuales

### payment_capacity

Calcula capacidad de descuento read-only para libranza y conserva las funciones
legacy de adelanto de nomina.

Reglas implementadas:

- normaliza ingreso base, descuentos actuales, cuota vigente y cuota proyectada
- calcula capacidad maxima por porcentaje configurable
- calcula cuota maxima permitida
- calcula capacidad disponible despues de descuentos y cuota vigente
- calcula porcentaje comprometido
- rechaza si no hay ingreso base valido
- rechaza si la cuota proyectada supera la capacidad maxima
- rechaza si descuentos/cuota vigente/cuota proyectada exceden la capacidad

DTO principal:

- `LibranzaCapacityInput`
- `LibranzaCapacityResult`

Salida principal:

- `ingreso_base`
- `descuentos_actuales`
- `cuota_actual_libranza`
- `cuota_proyectada`
- `capacidad_maxima`
- `capacidad_disponible`
- `cuota_maxima_permitida`
- `porcentaje_comprometido`
- `eligible`
- `reason`

### legal_rules

Evalua reglas base de producto/ley de libranza sin writes ni dependencias
externas.

Reglas implementadas:

- monto minimo de solicitud
- monto maximo de libranza
- capacidad de descuento requerida para la cuota proyectada
- calculo deterministico de primera fecha de pago
- helpers de plazo, tasa y fechas de vencimiento

DTO principal:

- `LibranzaLegalInput`
- `LibranzaLegalDecision`

### payer_validation

Validacion read-only de empresa, convenio activo, tipo de empresa y vinculo
laboral validado.

### special_cases

Servicio puro de simulacion para futuros casos especiales administrativos de
libranza. No importa modelos, no escribe datos y no esta conectado todavia a
vistas, formularios, originacion ni pagaré.

Limites especiales:

- monto maximo: `100000000`
- plazo maximo: `48` meses
- tasa mensual editable, no negativa, maximo `10.00` y con maximo 2 decimales
- comision editable por porcentaje, valor fijo o la suma de ambos
- IVA opcional sobre comision

DTO principal:

- `SpecialCaseSimulationInput`
- `SpecialCaseSimulationResult`

Salida principal:

- `requested_amount`
- `commission_amount`
- `vat_amount`
- `principal_financed`
- `estimated_interest`
- `total_to_pay`
- `monthly_payment`
- `monthly_rate`
- `term_months`

Auditoria implementada:

- modelo `CreditoReglaEspecialAudit` en `gestion_creditos`
- helper `create_special_case_audit`
- helper `serialize_simulation_result`
- soporte para simulaciones sin credito asociado
- payload financiero serializado para trazabilidad

UI interna implementada:

- formulario `SpecialCaseLibranzaSimulationForm`
- vista staff en `/gestion/libranza/casos-especiales/simular/`
- requiere permiso `gestion_creditos.can_originate_special_libranza`
- simulacion auditada obligatoria
- originacion controlada desde auditoria existente en
  `/gestion/libranza/casos-especiales/<audit_id>/originar/`
- servicio `originate_special_case_libranza`
- crea `Credito` y `CreditoLibranza` en estado `EN_REVISION`
- vincula `CreditoReglaEspecialAudit.credito`
- registra `HistorialEstado` con el usuario originador
- no envia pagare, no desembolsa y no activa creditos

## Policies

Las policies puras viven en `libranza.policies`:

- porcentaje configurable de capacidad de descuento
- capacidad maxima
- porcentaje comprometido
- codigos de rechazo/aprobacion de capacidad

## Selectors

`libranza.selectors` contiene queries read-only para empresa, convenio, vinculo
laboral y credito libranza vigente. No debe hacer writes ni disparar workflows.

## Relacion con risk

`libranza` calcula capacidad de descuento y reglas propias del producto.
`risk` decide escenarios de riesgo como segundo credito o recogida de cartera.
La integracion futura debe pasar los resultados de libranza hacia `risk` como
contexto, sin mezclar reglas de producto con politicas generales de riesgo.

## Supuestos actuales

- El porcentaje de capacidad usa `LIBRANZA_CAPACIDAD_DESCUENTO_PORCENTAJE` si
  existe; si no, usa `ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE`; por defecto 25%.
- La capacidad maxima se calcula sobre `ingreso_base`.
- La capacidad disponible resta descuentos actuales y cuota libranza vigente.
- La cuota proyectada representa la nueva cuota a evaluar, no el monto del
  credito.
- El selector de credito vigente usa creditos de linea `LIBRANZA` en estados
  operativamente bloqueantes o activos.
- Los wrappers legacy de `gestion_creditos.services.capacidad_descuento_service`
  se mantienen compatibles.

## Falta antes de produccion

- Politicas por pagaduria/convenio.
- Validacion legal completa contra salario minimo y topes normativos finos.
- Integracion con originacion, dashboards, WhatsApp y workflows.
- Versionado de decision.
- Accion posterior para enviar a flujo de firma despues de revision/pagador.
- DataCredito y scoring externo como adaptadores separados.

## No conectado todavia

- endpoints
- WhatsApp
- originacion productiva
- writes
- migraciones
- DataCredito
