from django.db import migrations


def merge_landing_logo_into_logo(apps, schema_editor):
    Empresa = apps.get_model('gestion_creditos', 'Empresa')
    for empresa in Empresa.objects.exclude(landing_logo='').exclude(landing_logo__isnull=True):
        if empresa.logo:
            continue
        empresa.logo = empresa.landing_logo
        empresa.save(update_fields=['logo'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0026_alter_empresa_logo'),
    ]

    operations = [
        migrations.RunPython(merge_landing_logo_into_logo, noop_reverse),
        migrations.RemoveField(
            model_name='empresa',
            name='landing_logo',
        ),
    ]
