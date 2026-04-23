from importlib import import_module

__all__ = [
    'aplicar_pago_obligaciones_seleccionadas',
    'build_full_name_upper',
    'build_obligaciones_pendientes_empresa',
    'backfill_accounting_for_credito',
    'calcular_total_en_mora',
    'filtrar_creditos',
    'get_accounting_summary_for_creditos',
    'get_admin_dashboard_context',
    'get_billetera_context',
    'get_platform_disbursed_creditos_queryset',
    'normalize_name_upper',
    'procesar_pagos_masivos_csv',
    'registrar_pago_credito',
    'saldar_credito_formalmente',
]

_EXPORTS = {
    'aplicar_pago_obligaciones_seleccionadas': ('gestion_creditos.services.payments', 'aplicar_pago_obligaciones_seleccionadas'),
    'backfill_accounting_for_credito': ('gestion_creditos.services.accounting', 'backfill_accounting_for_credito'),
    'build_full_name_upper': ('gestion_creditos.services.name_normalization', 'build_full_name_upper'),
    'build_obligaciones_pendientes_empresa': ('gestion_creditos.services.payments', 'build_obligaciones_pendientes_empresa'),
    'calcular_total_en_mora': ('gestion_creditos.services.dashboard_metrics', 'calcular_total_en_mora'),
    'filtrar_creditos': ('gestion_creditos.credit_services', 'filtrar_creditos'),
    'get_accounting_summary_for_creditos': ('gestion_creditos.services.accounting', 'get_accounting_summary_for_creditos'),
    'get_admin_dashboard_context': ('gestion_creditos.services.dashboard_metrics', 'get_admin_dashboard_context'),
    'get_billetera_context': ('gestion_creditos.credit_services', 'get_billetera_context'),
    'get_platform_disbursed_creditos_queryset': ('gestion_creditos.services.accounting', 'get_platform_disbursed_creditos_queryset'),
    'normalize_name_upper': ('gestion_creditos.services.name_normalization', 'normalize_name_upper'),
    'procesar_pagos_masivos_csv': ('gestion_creditos.credit_services', 'procesar_pagos_masivos_csv'),
    'registrar_pago_credito': ('gestion_creditos.credit_services', 'registrar_pago_credito'),
    'saldar_credito_formalmente': ('gestion_creditos.services.credit_lifecycle', 'saldar_credito_formalmente'),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
