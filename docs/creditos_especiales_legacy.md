# Creditos Especiales de Libranza: Carga y Visualizacion

## Como se representan hoy
Los creditos especiales usan el mismo modelo `Credito`, pero con:
- `tipo_regla_credito = ESPECIAL`
- `fecha_primera_cuota_forzada`
- `plazo_forzado`
- `tasa_forzada`
- `observacion_regla_especial`

Esto evita mezclar reglas especiales con la logica normal de primera cuota y permite marcarlos visualmente en dashboards.

## Que datos del archivo actual si se pueden usar
- Cedula
- Nombre
- Fecha desembolso
- Fecha primer pago
- Valor cuota
- Numero total de cuotas
- Cuotas pagadas
- Cuotas pendientes

## Que datos faltan para crear creditos reales sin inventar informacion
- Empresa / convenio del empleado
- Monto aprobado real o capital inicial
- Tasa mensual real del credito
- Porcentaje/comision real
- Datos de contacto completos si se quiere alta completa del detalle

## Regla operativa
Con la informacion actual no se debe crear automaticamente un credito financiero definitivo.
Primero se previsualiza y se completa lo faltante.

## Herramienta disponible
Usa el comando:

```powershell
python manage.py previsualizar_creditos_especiales_libranza --archivo ruta/al/archivo.xlsx --report-file reports/creditos_especiales_preview.json
```

El comando:
- normaliza fechas y montos
- valida consistencia de cuotas
- infiere proximo pago
- reporta faltantes para alta real

## Visualizacion
Los dashboards de libranza y pagador muestran badge de `Credito especial` cuando `tipo_regla_credito = ESPECIAL`.
