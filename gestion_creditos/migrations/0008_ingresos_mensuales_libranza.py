from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0007_wompi_intent'),
    ]

    operations = [
        migrations.AddField(
            model_name='creditolibranza',
            name='ingresos_mensuales',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Ingresos mensuales'),
        ),
        migrations.AlterField(
            model_name='creditolibranza',
            name='certificado_laboral',
            field=models.FileField(blank=True, null=True, upload_to='credito_libranza/certificados_laborales/', verbose_name='Certificado laboral'),
        ),
        migrations.AlterField(
            model_name='creditolibranza',
            name='desprendible_nomina',
            field=models.FileField(blank=True, null=True, upload_to='credito_libranza/desprendibles_nomina/', verbose_name='Desprendible de nómina'),
        ),
    ]
