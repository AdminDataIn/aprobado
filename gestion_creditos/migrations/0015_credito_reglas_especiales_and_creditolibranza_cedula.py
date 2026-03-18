from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0014_alter_creditolibranza_certificado_bancario_estado_extraccion'),
    ]

    operations = [
        migrations.AddField(
            model_name='credito',
            name='fecha_primera_cuota_forzada',
            field=models.DateField(
                blank=True,
                help_text='Fecha manual de primera cuota para creditos especiales.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='credito',
            name='observacion_regla_especial',
            field=models.TextField(
                blank=True,
                help_text='Justificacion operativa de la regla especial.',
            ),
        ),
        migrations.AddField(
            model_name='credito',
            name='plazo_forzado',
            field=models.IntegerField(
                blank=True,
                help_text='Plazo aplicado por regla especial, si difiere del aprobado.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='credito',
            name='tasa_forzada',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Tasa mensual aplicada por regla especial.',
                max_digits=5,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='credito',
            name='tipo_regla_credito',
            field=models.CharField(
                choices=[('NORMAL', 'Normal'), ('ESPECIAL', 'Especial')],
                default='NORMAL',
                help_text='Permite modelar creditos especiales sin excepciones invisibles.',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='creditolibranza',
            name='cedula',
            field=models.CharField(max_length=20, verbose_name='Numero de cedula'),
        ),
    ]
