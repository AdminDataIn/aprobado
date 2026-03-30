# Operacion de Carga de Empleados

## Objetivo
- La elegibilidad de Libranza y Adelanto de nomina ya no depende de digitacion libre del empleado.
- El pagador carga la base laboral y esa carga alimenta `VinculoLaboralEmpresa`.

## Plantilla
Campos requeridos:
- `tipo_documento`
- `documento`
- `nombres`
- `apellidos`
- `correo`
- `celular`
- `salario_base`
- `auxilio_transporte`
- `descuentos_fijos`
- `fecha_alta_aprobado`
- `estado_vinculo`
- `convenio_activo`

## Flujo
1. Pagador descarga plantilla desde `/pagador/empleados/plantilla/` (por defecto en Excel `.xlsx`; CSV disponible como compatibilidad)
2. Sube archivo en `/pagador/empleados/cargar/`
3. El sistema crea o actualiza `User`
4. El sistema crea o actualiza `VinculoLaboralEmpresa`
5. Si la empresa era solo marketplace externa y la carga indica convenio, pasa a `MIXTA`

## Legacy
- El boton de reconciliacion revisa vinculos ya existentes y completa email/nombre faltante del usuario.
- No crea usuarios duplicados por correo.

