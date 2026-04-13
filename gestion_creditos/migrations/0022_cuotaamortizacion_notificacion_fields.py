from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0021_alter_historialpago_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cuotaamortizacion',
            name='fecha_ultimo_aviso_usuario_mora',
            field=models.DateTimeField(blank=True, help_text='Ultima vez que se notifico al usuario por atraso posterior al resumen al pagador.', null=True),
        ),
        migrations.AddField(
            model_name='cuotaamortizacion',
            name='fecha_ultimo_recordatorio_pagador',
            field=models.DateTimeField(blank=True, help_text='Ultima vez que se incluyo esta cuota en el resumen mensual al pagador.', null=True),
        ),
    ]
