from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0003_pagadoraccesstoken'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductAccessProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('flow', models.CharField(choices=[('LIBRANZA', 'Libranza'), ('EMPRENDIMIENTO', 'Emprendimiento'), ('INVERSIONISTA', 'Inversionista'), ('MARKETPLACE_BUYER', 'Marketplace buyer')], max_length=30)),
                ('locked_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('usuario', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='product_access_profile', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Perfil de acceso por producto',
                'verbose_name_plural': 'Perfiles de acceso por producto',
            },
        ),
    ]
