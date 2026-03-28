# Ajuste Seguro de Fechas de Pago

## Regla operativa
- Aprobado antes del dia 15: primera cuota el 1 del mes siguiente.
- Aprobado desde el dia 15 en adelante: primera cuota el 1 del mes subsiguiente.

## Implementacion
- Regla central: `gestion_creditos/services/libranza_rules.py`
- Comando seguro: `python manage.py ajustar_fechas_pago_libranza`

## Uso
Dry-run:
```bash
python manage.py ajustar_fechas_pago_libranza --creditos CR-001 CR-002 CR-003 --excluir-creditos CR-ESP-001
```

Aplicar:
```bash
python manage.py ajustar_fechas_pago_libranza --creditos CR-001 CR-002 CR-003 --excluir-creditos CR-ESP-001 --apply
```

Reporte JSON opcional:
```bash
python manage.py ajustar_fechas_pago_libranza --creditos CR-001 CR-002 CR-003 --excluir-creditos CR-ESP-001 --report-file reports/ajuste_fechas.json
```

## Notas
- Solo ajusta los creditos listados.
- El credito especial debe excluirse por `numero_credito`.
- Si el credito ya esta marcado como `ESPECIAL`, el comando tambien lo omite.
- Se reprograman las cuotas pendientes sin tocar cuotas ya pagadas.
- Si el credito tiene pagos exitosos, cuotas pagadas o reestructuraciones, el comando lo omite y lo deja para revision manual.
