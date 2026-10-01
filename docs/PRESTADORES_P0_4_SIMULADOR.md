# P0-4: simulador y captura mobile

## Disponibilidad

`obtener_configuracion_simulador_prestador()` selecciona la configuracion financiera
de la politica score activa y vigente, si existe. Esa configuracion debe estar
activa y versionada. Sin politica vigente selecciona la unica configuracion
financiera activa con version no vacia. No modifica ni activa score.

Una configuracion faltante, inactiva, sin version, o una politica vigente con enlace
invalido explica el estado no disponible. No puede determinarse cual ocurre en
produccion sin consultar su configuracion. No se consulto ni modifico produccion.

El Admin existente permite configurarla en
`/admin/contractors/configuracionsimuladorprestador/`, con sus permisos habituales.
No basta crear una fila con los defaults: requiere version y parametros aprobados.
Si existe politica vigente, revisar su enlace sin sustituirla automaticamente.

## Parametros documentados y habilitacion

La seccion 27 de `prestadores_flujo_end_to_end.md` documenta los siguientes valores
**DEMO**, expresamente no aprobados como politica productiva. El default historico
del modelo (1.9%) no equivale a la tasa documentada (2.2%). No se activan al desplegar.

Ejemplo JSON para validacion/UAT con esos parametros:

```json
{
  "version": "prestadores-simulador-uat-v1",
  "monto_minimo": "1000000",
  "monto_maximo": "10000000",
  "plazo_minimo_meses": 3,
  "plazo_maximo_meses": 8,
  "tasa_mensual": "2.2000",
  "porcentaje_originacion": "10.0000",
  "porcentaje_iva_originacion": "19.0000",
  "porcentaje_fondo_garantia": "2.0000",
  "porcentaje_seguro_vida_primera_cuota": "0.3711"
}
```

El operador suministra el JSON autorizado. Primero confirmar entorno/BD y validar:

```sh
python manage.py configurar_simulador_prestadores --archivo parametros.json
```

La validacion usa una transaccion revertida, sin conservar cambios. Para persistir,
solo tras autorizacion de los parametros y del entorno:

```sh
python manage.py configurar_simulador_prestadores --archivo parametros.json --aplicar
```

El comando no reemplaza otra configuracion activa ni reescribe una version existente.
Repetir con iguales valores es idempotente. Rechaza el enlace incompatible de una
politica vigente y revierte la operacion. No crea bandas, score, creditos ni cuotas.
No ejecutar el bootstrap de politica DEMO para habilitar solamente este simulador.

## Verificacion

Solicitud propia con documentos y analisis vigentes -> GET simulacion -> calculo
backend con `calcular_componentes_financieros` -> POST monto/plazo -> snapshot
persistido y Mi credito. Sin configuracion: mensaje seguro, calculo HTTP 503 y sin
fallback financiero. No se declara cerrado el E2E de riesgo/originacion.

El mojibake estaba en literales del template de ausencia de configuracion; se
corrigieron en UTF-8 y se prueban charset HTTP, meta charset y ambas ramas del HTML.

La camara conserva object-fit contain y resolucion intrinseca; el layout inmersivo
usa dvh/safe-area, guia ID-1 y controles superpuestos. El cambio de camara detiene
el stream anterior y reutiliza la sesion; no altera canje, TTL, polling ni retorno.
Playwright no sustituye la validacion fisica de enfoque/permisos en iPhone/Safari.

## P0-4C.2: semantica aprobada y plantilla PROD pendiente

Definiciones recibidas el 2026-09-30: monto minimo $500.000, maximo $3.000.000,
tasa mensual 1,0000%, originacion 10%, IVA 19% exclusivamente sobre originacion,
fondo Figarantias 2%. Seguro SURA financiado, porcentaje aun NO aprobado.
El plazo del ejemplo (3) no define limites productivos.

Plantilla: `docs/parametros_prestadores_prod_pendientes.json`. Version, seguro,
plazo minimo y plazo maximo permanecen `null`. El comando rechaza cualquier
parametro requerido nulo antes de consultar/escribir configuracion. No se
aplico esta plantilla, no se activaron defaults ni DEMO como politica productiva.
La seleccion de configuraciones existentes se conserva; desplegar este codigo
NO convierte una configuracion DEMO existente en configuracion PROD aprobada.
Antes de habilitar produccion debe verificarse explicitamente la version activa.

Unica formula: `gestion_creditos.services.condiciones_financieras.calcular_componentes_financieros`.
El simulador y la capacidad preliminar usan esa funcion a traves de
`simular_credito_prestador_informativo`; ya no se calcula cuota sobre monto base
sin cargos. Monto/plazo fuera de configuracion no producen una cuota de capacidad.
No se ha implementado ni ejecutado score, centrales o predecision en este bloque.

Desembolso neto = monto solicitado (redondeado a centavos). Originacion, su IVA,
fondo y seguro se financian dentro del capital y generan intereses; ninguno se
descuenta del desembolso ni se cobra separado o exclusivamente en la primera cuota.
Se conserva `porcentaje_seguro_vida_primera_cuota` como nombre historico compatible
con BD, JSON y snapshots. No describe una prima exclusiva de la primera cuota.
No se renombra ni modifica su default historico mediante migracion; este default
NO es aprobacion del porcentaje SURA productivo. `comision` sigue como nombre
interno del core compartido; la UX Prestadores usa originacion.

Calculo en Decimal con ROUND_HALF_UP a centavos. Ejemplo de test, NO configuracion
productiva: monto 1.000.000, seguro 3.711, plazo 3, tasa mensual 1%. Capital
1.142.711, cuota 388.547,01, total 1.165.641,03, intereses 22.930,03. El centavo de
diferencia frente a la referencia aproximada se debe a total = cuota redondeada
por numero de cuotas. No se altera la formula ni los snapshots historicos.

El navegador no replica la formula con floats: solicita el resultado al endpoint
existente y solo formatea los importes. Al cambiar sliders invalida resultados
anteriores y descarta respuestas atrasadas; sin respuesta no inventa una cuota.
La UX muestra monto recibido, cargos financiados, tasa, capital, cuota, numero de
cuotas y total con centavos.

P0-4E.2 sigue obligatorio antes de P0-5: saldo declarado no prueba pagos recibidos;
separar causado, exigible, recibido/verificado y flujo futuro. Esta alineacion de
cuota no certifica capacidad final. La brecha de auditoria actor/motivo del comando
identificada en P0-4C.1 no se corrige ni se da por cerrada en este ticket.
