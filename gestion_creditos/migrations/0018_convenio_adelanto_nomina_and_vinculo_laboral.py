from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0017_marketplace_transacciones_investor_domain_and_empresa_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='empresa',
            name='convenio_activo',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='credito',
            name='linea',
            field=models.CharField(
                choices=[
                    ('EMPRENDIMIENTO', 'Emprendimiento'),
                    ('LIBRANZA', 'Libranza'),
                    ('ADELANTO_NOMINA', 'Adelanto de Nomina'),
                ],
                max_length=30,
            ),
        ),
        migrations.CreateModel(
            name='VinculoLaboralEmpresa',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('documento_empleado', models.CharField(max_length=20)),
                ('nombre_empleado', models.CharField(max_length=160)),
                ('correo_empleado', models.EmailField(blank=True, max_length=254)),
                ('telefono_empleado', models.CharField(blank=True, max_length=20)),
                ('estado_vinculo', models.CharField(choices=[('ACTIVO', 'Activo'), ('INACTIVO', 'Inactivo'), ('SUSPENDIDO', 'Suspendido')], default='ACTIVO', max_length=20)),
                ('fecha_alta_aprobado', models.DateField()),
                ('salario_base_mensual', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('validado_por_pagador', models.BooleanField(default=False)),
                ('observaciones', models.TextField(blank=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='vinculos_laborales', to='gestion_creditos.empresa')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='vinculos_laborales', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Vinculo laboral con empresa',
                'verbose_name_plural': 'Vinculos laborales con empresas',
                'ordering': ['-fecha_alta_aprobado', '-creado_en'],
            },
        ),
        migrations.CreateModel(
            name='CreditoAdelantoNomina',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monto_solicitado', models.DecimalField(decimal_places=2, max_digits=12)),
                ('monto_maximo_calculado', models.DecimalField(decimal_places=2, max_digits=12)),
                ('dias_adelanto', models.PositiveSmallIntegerField(default=5)),
                ('salario_base_usado', models.DecimalField(decimal_places=2, max_digits=12)),
                ('motivo_bloqueo', models.CharField(blank=True, max_length=255)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('credito', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='detalle_adelanto_nomina', to='gestion_creditos.credito')),
                ('vinculo_laboral', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='creditos_adelanto', to='gestion_creditos.vinculolaboralempresa')),
            ],
            options={
                'verbose_name': 'Detalle de adelanto de nomina',
                'verbose_name_plural': 'Detalles de adelanto de nomina',
            },
        ),
        migrations.AddConstraint(
            model_name='vinculolaboralempresa',
            constraint=models.UniqueConstraint(fields=('usuario', 'empresa'), name='uniq_vinculo_laboral_usuario_empresa'),
        ),
    ]
