# Cambios Estructurales Implementados

## gestion_creditos.models.Empresa
- `convenio_activo` (bool): fuente operativa para libranza.
- `tipo_empresa` (`CONVENIO`, `MARKETPLACE_EXTERNA`, `MIXTA`): separa convenio vs marketplace.
- `logo`, `correo_contacto`, `telefono_contacto`, `marketplace_fee_percent`, `pagos_habilitados`, `mp_*`: soporte marketplace y branding.

## gestion_creditos.models.Credito
- Nueva linea: `ADELANTO_NOMINA`.
- Regla especial ya soportada con:
  - `tipo_regla_credito`
  - `fecha_primera_cuota_forzada`
  - `plazo_forzado`
  - `tasa_forzada`
  - `observacion_regla_especial`
- Impacto: permite representar creditos legacy/especiales sin romper la regla normal de libranza.

## gestion_creditos.models.VinculoLaboralEmpresa
Campos agregados / operativos:
- `tipo_documento`
- `documento_empleado`
- `nombre_empleado`
- `correo_empleado`
- `telefono_empleado`
- `salario_base_mensual`
- `auxilio_transporte_mensual`
- `descuentos_fijos_mensuales`
- `fecha_alta_aprobado`
- `estado_vinculo`
- `validado_por_pagador`
- `cargado_por`
- `observaciones`

Impacto: la elegibilidad de libranza y adelanto deja de depender de digitacion libre del empleado.

## gestion_creditos.models.CreditoAdelantoNomina
- Relaciona el credito de adelanto con su `VinculoLaboralEmpresa`.
- Guarda monto solicitado, monto maximo calculado, salario usado y dias de adelanto.

## usuarios.models
- `ProductAccessProfile`: separa flujos de acceso por producto.
- `InvestorAccessToken`: activacion segura para inversionista.
- `PagadorAccessToken`: activacion / reset para pagador.

## Migraciones nuevas presentes
### gestion_creditos
- `0017_marketplace_transacciones_investor_domain_and_empresa_fields.py`
- `0018_convenio_adelanto_nomina_and_vinculo_laboral.py`
- `0019_empresa_logo.py`
- `0020_empresa_tipo_empresa_and_more.py`

### usuarios
- `0005_investoraccesstoken.py`

## Antes de subir al servidor
1. Ejecutar `python manage.py migrate`.
2. Validar variables de entorno del flujo libranza/adelanto.
3. Reiniciar web + celery si cambian flags operativas.
4. Verificar que el servidor no sobreescriba personalizaciones locales de `settings.py` sin revision.
