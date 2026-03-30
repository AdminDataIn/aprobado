from django.core.checks import Warning, register
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


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
