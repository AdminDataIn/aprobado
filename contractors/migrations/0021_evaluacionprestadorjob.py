from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('contractors', '0020_ingreso_neto_verificado_prestador'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EvaluacionPrestadorJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('clave_contexto', models.CharField(max_length=64, unique=True)),
                ('contexto', models.JSONField(default=dict)),
                ('estado', models.CharField(choices=[('PENDIENTE_PUBLICACION', 'Pendiente de publicacion'), ('ENCOLADA', 'Encolada'), ('EN_EJECUCION', 'En ejecucion'), ('FINALIZADA', 'Finalizada'), ('REQUIERE_REVISION', 'Requiere revision'), ('SUSPENDIDA_OPERATIVA', 'Suspendida'), ('OBSOLETA', 'Obsoleta')], default='PENDIENTE_PUBLICACION', max_length=24)),
                ('codigo_operativo', models.CharField(blank=True, max_length=64)),
                ('intentos_publicacion', models.PositiveIntegerField(default=0)),
                ('proxima_publicacion', models.DateTimeField()),
                ('publicada_en', models.DateTimeField(blank=True, null=True)),
                ('iniciada_en', models.DateTimeField(blank=True, null=True)),
                ('finalizada_en', models.DateTimeField(blank=True, null=True)),
                ('lease_hasta', models.DateTimeField(blank=True, null=True)),
                ('generacion', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('auditoria', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='contractors.predecisionprestadoraudit')),
                ('solicitud', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='evaluacion_jobs', to='contractors.contractorapplication')),
            ],
            options={'indexes': [models.Index(fields=['estado', 'proxima_publicacion'], name='prest_job_dispatch_idx')]},
        ),
    ]
