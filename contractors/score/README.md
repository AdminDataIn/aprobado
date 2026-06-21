# Score interno read-only para prestadores

Este modulo calcula un score interno preliminar para solicitudes de
prestadores de servicios. El resultado es read-only: no aprueba, no rechaza
productivamente, no crea creditos y no modifica solicitudes.

## Configuracion versionada

La version activa por defecto es:

- `CONFIGURACION_SCORE_PRESTADORES_V2`
- `version = prestadores_score_v2_2026_06`

La version activa se resuelve con:

```python
obtener_configuracion_score_prestadores()
```

El selector lee `CONTRACTORS_SCORE_CONFIG_VERSION`. Si se solicita una version
inexistente, falla de forma controlada y no hace fallback silencioso.

`CONFIGURACION_SCORE_PRESTADORES_V1` se mantiene intacta para compatibilidad e
historicos. No debe borrarse ni modificarse para recalibrar V2.

## Politica V2

Parametros de producto:

- monto maximo: `$10.000.000`
- plazo maximo: `8` meses
- tasa mensual fija: `2.2000%`
- TEA: calculada desde la tasa mensual
- regla financiera: `cuota_proyectada / ingreso_disponible <= 0.30`
- `ingreso_disponible = ingreso_neto - obligaciones_mensuales`

Pesos:

- DataCredito: `0.45`
- capacidad contractual: `0.30`
- comportamiento digital: `0.08`
- riesgo fraude: `0.12`
- referencias: `0.05`
- geolocalizacion: `0.00`

Geolocalizacion no suma score. Si el puntaje geografico es menor a `600`,
aplica penalizacion de `-80`.

Bandas:

- `PREMIUM`: 850-1000, hasta `$10.000.000`, plazo 8
- `ALTA`: 750-849, hasta `$8.000.000`, plazo 8
- `MEDIA`: 680-749, hasta `$5.000.000`, plazo 8
- `ENTRADA`: 600-679, hasta `$3.000.000`, plazo 6
- `REVISION`: 0-599, sin monto ni plazo sugerido

## Capacidad financiera

El motor V2 puede recortar monto/plazo por:

- monto solicitado;
- valor pendiente por cobrar del contrato;
- meses restantes del contrato;
- cupo financiero calculado con tasa mensual 2.2000%;
- regla cuota/ingreso de 30%.

Si no existen `ingreso_neto` u `obligaciones_mensuales` formales, el motor no
inventa capacidad financiera. Mantiene los topes por banda/contrato y registra
la advertencia `capacidad_financiera_sin_ingreso_u_obligaciones_formales`.

La transformacion de capacidad contractual a componente de score sigue usando
la tabla por uso de capacidad vigente. Queda pendiente calibrarla con datos
historicos reales.

## DataCredito

El score recibe DataCredito como componente opcional. La predecision orquesta el
adapter y le entrega al motor un puntaje normalizado cuando esta disponible. Si
DataCredito no esta disponible, el componente queda pendiente y la decision
preliminar requiere revision manual.

## Alineacion del portal

Para alinear la configuracion financiera visible del portal a V2 se usa un
comando explicito, sin data migration automatica:

```bash
venv\Scripts\python.exe manage.py configurar_politica_prestadores_v2 --host contratistas.localhost
venv\Scripts\python.exe manage.py configurar_politica_prestadores_v2 --host contratistas.localhost --confirmar
```

Sin `--confirmar`, el comando solo muestra diferencias.


Paso 3. Ver el cambio de configuración local sin aplicarlo
venv\Scripts\python.exe manage.py configurar_politica_prestadores_v2 `
  --host contratistas.localhost

Lee cuidadosamente el diff.

Paso 4. Aplicarlo localmente
venv\Scripts\python.exe manage.py configurar_politica_prestadores_v2 `
  --host contratistas.localhost `
  --confirmar
Paso 5. Comprobar la base de datos
venv\Scripts\python.exe manage.py shell -c "
from contractors.models import ConfiguracionPortalContratistas

portal = ConfiguracionPortalContratistas.objects.get(
    host='contratistas.localhost'
)

print('Host:', portal.host)
print('Monto mínimo:', portal.monto_minimo)
print('Monto máximo:', portal.monto_maximo)
print('Plazo mínimo:', portal.plazo_minimo_meses)
print('Plazo máximo:', portal.plazo_maximo_meses)
print('Tasa mensual:', portal.tasa_mensual)
print('Comisión:', portal.tasa_comision)
print('IVA:', portal.tasa_iva)
"

Debes confirmar:

Monto máximo: 10000000.00
Plazo máximo: 8
Tasa mensual: 2.2000