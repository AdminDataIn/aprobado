from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0012_alter_movimientoahorro_estado_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='creditolibranza',
            name='certificado_bancario_estado_extraccion',
            field=models.CharField(
                choices=[
                    ('pendiente', 'Pendiente'),
                    ('procesado', 'Procesado'),
                    ('parcial', 'Parcial'),
                    ('fallido', 'Fallido'),
                ],
                default='pendiente',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='creditolibranza',
            name='certificado_bancario_metadata',
            field=models.JSONField(
                blank=True,
                default=dict,
            ),
        ),
        migrations.AddField(
            model_name='creditolibranza',
            name='certificado_bancario_ultima_extraccion',
            field=models.DateTimeField(
                blank=True,
                null=True,
            ),
        ),
    ]
