import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('contractors', '0022_configuracionscoreprestador_politica_ingreso_evaluable'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ArchivoSolicitudPrestador',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('archivada_en', models.DateTimeField(auto_now_add=True)),
                ('motivo', models.CharField(max_length=500)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+',
                                            to=settings.AUTH_USER_MODEL)),
                ('solicitud', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                                  related_name='archivo_operativo', to='contractors.contractorapplication')),
            ],
            options={'permissions': [('can_archive_contractor_application', 'Puede archivar solicitudes de prestadores')]},
        ),
    ]
