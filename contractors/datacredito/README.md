# Adapter DataCredito read-only para prestadores

Este modulo prepara la integracion futura con DataCredito para el flujo de
prestadores de servicios, sin hacer consultas productivas reales todavia.

## Objetivo

- Aislar el proveedor externo del dominio `contractors`.
- Permitir pruebas con proveedor `mock`.
- Alimentar el score interno read-only sin acoplar el motor al proveedor.
- Mantener metadata segura sin documento completo ni respuesta cruda.

## Configuracion

Variables:

- `CONTRACTORS_DATACREDITO_ENABLED=False`
- `CONTRACTORS_DATACREDITO_PROVIDER=mock`
- `CONTRACTORS_DATACREDITO_TIMEOUT_SECONDS=10`
- `CONTRACTORS_DATACREDITO_MOCK_SCENARIO=bueno`

Proveedores:

- `no_configurado`: default si no esta habilitado.
- `mock`: escenarios controlados para pruebas.
- `real`: reservado; no hace llamada externa hasta tener contrato tecnico.

## Resultado sanitizado

`ResultadoDatacreditoPrestador` expone:

- disponibilidad;
- fuente;
- score externo y score normalizado 0-1000;
- mora severa y mora actual;
- obligaciones abiertas y en mora;
- nivel de riesgo;
- alertas;
- revision manual;
- error tecnico controlado;
- metadata segura con documento enmascarado y hash.

No se guarda ni retorna:

- respuesta cruda del proveedor;
- XML;
- JSON completo;
- documento completo;
- credenciales.

## Escenarios mock

- `bueno`
- `medio`
- `mora_severa`
- `no_disponible`

## Integracion con score

La predecision orquesta:

1. consulta DataCredito read-only;
2. pasa `score_normalizado_0_1000` al componente `datacredito` si esta disponible;
3. mantiene `PENDIENTE` si no esta disponible;
4. bloquea read-only si hay mora severa;
5. nunca crea credito ni cambia estados.

## Pendiente

- contrato tecnico del proveedor real;
- mapper de respuesta real a DTO sanitizado;
- timeouts/reintentos reales;
- trazabilidad auditada de consentimiento;
- persistencia historica controlada si negocio la aprueba.
