from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('gestion_creditos', '0023_detallecontablepago'),
    ]

    operations = [
        migrations.CreateModel(
            name='AsesorComercial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=160)),
                ('cedula', models.CharField(max_length=20, unique=True)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('telefono', models.CharField(blank=True, max_length=20)),
                ('activo', models.BooleanField(default=True)),
                ('observaciones', models.TextField(blank=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('usuario', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='asesor_comercial', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Asesor comercial',
                'verbose_name_plural': 'Asesores comerciales',
                'ordering': ['nombre'],
            },
        ),
        migrations.AddField(
            model_name='empresa',
            name='asesor_comercial',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='empresas_referidas', to='gestion_creditos.asesorcomercial'),
        ),
        migrations.AddIndex(
            model_name='asesorcomercial',
            index=models.Index(fields=['activo', 'nombre'], name='asesor_activo_nombre_idx'),
        ),
    ]
