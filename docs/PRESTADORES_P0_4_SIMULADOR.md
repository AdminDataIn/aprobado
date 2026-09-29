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
