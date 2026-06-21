from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contractors', '0017_timelineprestador'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contractorapplication',
            name='requested_amount',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=14,
                null=True,
                validators=[django.core.validators.MinValueValidator(Decimal('0.01'))],
            ),
        ),
        migrations.AlterField(
            model_name='contractorapplication',
            name='term_months',
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
    ]
