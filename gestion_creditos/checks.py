from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Warning, register
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


def _ruta_contenida(ruta, contenedor):
    ruta = Path(ruta).resolve()
    contenedor = Path(contenedor).resolve()
    return ruta == contenedor or contenedor in ruta.parents


@register()
def check_private_documents_root(app_configs, **kwargs):
    private_root = settings.PRIVATE_DOCUMENTS_ROOT
    rutas_publicas = {
        'MEDIA_ROOT': settings.MEDIA_ROOT,
        'STATIC_ROOT': settings.STATIC_ROOT,
    }
    errores = []
    for nombre, ruta_publica in rutas_publicas.items():
        if ruta_publica and _ruta_contenida(private_root, ruta_publica):
            errores.append(Error(
                f'PRIVATE_DOCUMENTS_ROOT no puede estar dentro de {nombre}.',
                hint='Configura un directorio privado que el servidor web no publique.',
                id='gestion_creditos.E002',
            ))
    return errores


@register()
def check_pending_credit_domain_migrations(app_configs, **kwargs):
    connection = connections['default']
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:
        return []

    unapplied = {
        migration.name
        for migration, _backwards in plan
        if migration.app_label == 'gestion_creditos'
    }
    if '0018_convenio_adelanto_nomina_and_vinculo_laboral' in unapplied:
        return [
            Warning(
                'La migracion 0018 de gestion_creditos aun no esta aplicada.',
                hint='Ejecuta: python manage.py migrate',
                id='gestion_creditos.W001',
            )
        ]
    return []
