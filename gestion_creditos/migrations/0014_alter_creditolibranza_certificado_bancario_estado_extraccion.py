from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0013_creditolibranza_certificado_bancario_metadata'),
    ]

    operations = [
        migrations.AlterField(
            model_name='creditolibranza',
            name='certificado_bancario_estado_extraccion',
            field=models.CharField(
                choices=[
                    ('pendiente', 'Pendiente'),
                    ('completo', 'Completo'),
                    ('error', 'Error'),
                ],
                default='pendiente',
                max_length=20,
            ),
        ),
    ]
