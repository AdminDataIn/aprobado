from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0025_empresa_landing_logo'),
    ]

    operations = [
        migrations.AlterField(
            model_name='empresa',
            name='logo',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to='marketplace/logos/',
                validators=[django.core.validators.FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp', 'svg'])],
            ),
        ),
    ]
