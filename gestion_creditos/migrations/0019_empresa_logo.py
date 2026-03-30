from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0018_convenio_adelanto_nomina_and_vinculo_laboral'),
    ]

    operations = [
        migrations.AddField(
            model_name='empresa',
            name='logo',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to='marketplace/logos/',
                validators=[django.core.validators.FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])],
            ),
        ),
    ]
