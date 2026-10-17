from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('contractors', '0021_evaluacionprestadorjob')]

    operations = [
        migrations.AddField(
            model_name='configuracionscoreprestador',
            name='politica_ingreso_evaluable',
            field=models.JSONField(default=dict, db_default={}, blank=True),
        ),
    ]
