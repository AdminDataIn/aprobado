from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0024_asesorcomercial_empresa_asesor_comercial'),
    ]

    operations = [
        migrations.AddField(
            model_name='empresa',
            name='landing_logo',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to='landing/logos/',
                validators=[django.core.validators.FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp', 'svg'])],
            ),
        ),
    ]
