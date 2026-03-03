from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0014_alter_creditolibranza_certificado_bancario_estado_extraccion'),
        ('usuarios', '0002_perfilempresamarketing'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PagadorAccessToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('activacion', 'Activacion'), ('reset_password', 'Reset password')], default='activacion', max_length=30)),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('token_hint', models.CharField(blank=True, max_length=12)),
                ('email_destino', models.EmailField(max_length=254)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('invalidated_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pagador_tokens_creados', to=settings.AUTH_USER_MODEL)),
                ('perfil_pagador', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='access_tokens', to='usuarios.perfilpagador')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pagador_access_tokens', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='pagadoraccesstoken',
            index=models.Index(fields=['usuario', 'tipo'], name='pag_token_user_tipo_idx'),
        ),
        migrations.AddIndex(
            model_name='pagadoraccesstoken',
            index=models.Index(fields=['expires_at'], name='pag_token_exp_idx'),
        ),
    ]
