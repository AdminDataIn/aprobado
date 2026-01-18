import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0006_zapsignwebhooklog_pagare'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WompiIntent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('referencia', models.CharField(max_length=100)),
                ('amount_in_cents', models.BigIntegerField()),
                ('payment_method', models.CharField(blank=True, max_length=30)),
                ('status', models.CharField(choices=[('CREATED', 'Created'), ('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('DECLINED', 'Declined'), ('ERROR', 'Error'), ('EXPIRED', 'Expired')], default='CREATED', max_length=20)),
                ('wompi_transaction_id', models.CharField(blank=True, db_index=True, max_length=100, null=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, max_length=255)),
                ('referer', models.CharField(blank=True, max_length=255)),
                ('attempts', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('credito', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wompi_intentos', to='gestion_creditos.credito')),
                ('usuario', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Wompi Intent',
                'verbose_name_plural': 'Wompi Intents',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['credito', 'status'], name='wompi_int_c_s_idx'),
                    models.Index(fields=['referencia'], name='wompi_int_ref_idx'),
                ],
            },
        ),
    ]
