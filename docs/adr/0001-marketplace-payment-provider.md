# ADR 0001 - Pasarela de pagos para marketplace MVP

## Estado
Aceptado

## Contexto
El proyecto ya usa WOMPI para flujos crediticios, pero el marketplace actual solo cubre catalogo y publicacion. La siguiente fase requiere un checkout de una sola empresa por pedido, comision del marketplace y base de liquidacion a la empresa vendedora.

## Decision
- Marketplace MVP: **Mercado Pago**
- Flujos crediticios existentes: **WOMPI**
- Payouts/tesoreria futura: **Cobre** a evaluar cuando la operacion requiera dispersion programada o automatizada a escala

## Motivos
- Mercado Pago expone un modelo mas directo para marketplace con comision por operacion y onboarding del vendedor.
- WOMPI ya esta integrado y se conserva donde hoy aporta valor.
- Cobre encaja mejor como capa de treasury/payouts B2B que como checkout inicial del marketplace.

## Consecuencias
- El dominio transaccional del marketplace debe registrar pedido, pago, comision y liquidacion.
- La dispersion a la empresa en v1 puede seguir siendo manual, pero registrada en base de datos.
- La integracion con Mercado Pago se hara sobre pedidos de una sola empresa por checkout.

## Reevaluacion
Revisar esta decision si:
- se requiere carrito multiempresa,
- las tarifas comerciales negociadas cambian materialmente,
- o la operacion exige payouts automaticos/conciliacion mas profunda.
