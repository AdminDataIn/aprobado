from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contractors', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='contractorapplication',
            name='monto_solicitado',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=14,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal('0.01'))],
            ),
        ),
        migrations.AddField(
            model_name='contractorapplication',
            name='plazo_meses',
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AlterField(
            model_name='contractorapplication',
            name='estado',
            field=models.CharField(
                choices=[
                    ('BORRADOR', 'Borrador'),
                    ('DOCUMENTOS_PENDIENTES', 'Documentos pendientes'),
                    ('DOCUMENTOS_CARGADOS', 'Documentos cargados'),
                    ('EN_REVISION', 'En revision'),
                ],
                default='DOCUMENTOS_PENDIENTES',
                max_length=32,
            ),
        ),
    ]
