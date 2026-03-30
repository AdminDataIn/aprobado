from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_creditos', '0016_alter_credito_estado_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='empresa',
            name='correo_contacto',
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name='empresa',
            name='marketplace_fee_percent',
            field=models.DecimalField(decimal_places=2, default=Decimal('10.00'), max_digits=5),
        ),
        migrations.AddField(
            model_name='empresa',
            name='mp_access_token',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='empresa',
            name='mp_user_id',
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name='empresa',
            name='nit',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='empresa',
            name='pagos_habilitados',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='empresa',
            name='razon_social',
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name='empresa',
            name='representante_legal',
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name='empresa',
            name='telefono_contacto',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.CreateModel(
            name='InvestorAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('activa', models.BooleanField(default=True)),
                ('moneda', models.CharField(default='COP', max_length=10)),
                ('fecha_apertura', models.DateTimeField(auto_now_add=True)),
                ('fecha_actualizacion', models.DateTimeField(auto_now=True)),
                ('usuario', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='investor_account', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Cuenta inversionista',
                'verbose_name_plural': 'Cuentas inversionista',
            },
        ),
        migrations.CreateModel(
            name='MarketplacePedido',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('numero_pedido', models.CharField(editable=False, max_length=30, unique=True)),
                ('comprador_nombre', models.CharField(max_length=160)),
                ('comprador_email', models.EmailField(max_length=254)),
                ('comprador_telefono', models.CharField(blank=True, max_length=20)),
                ('estado', models.CharField(choices=[('borrador', 'Borrador'), ('pendiente_pago', 'Pendiente de pago'), ('pagado', 'Pagado'), ('en_gestion', 'En gestion'), ('completado', 'Completado'), ('cancelado', 'Cancelado')], default='borrador', max_length=20)),
                ('subtotal', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('marketplace_fee_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('total', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('moneda', models.CharField(default='COP', max_length=10)),
                ('external_reference', models.CharField(blank=True, max_length=100)),
                ('notas', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('comprador', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='marketplace_pedidos', to=settings.AUTH_USER_MODEL)),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='marketplace_pedidos', to='gestion_creditos.empresa')),
            ],
            options={
                'verbose_name': 'Pedido marketplace',
                'verbose_name_plural': 'Pedidos marketplace',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='InvestmentPosition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('referencia', models.CharField(blank=True, max_length=50, unique=True)),
                ('titulo', models.CharField(max_length=160)),
                ('estado', models.CharField(choices=[('borrador', 'Borrador'), ('activa', 'Activa'), ('cerrada', 'Cerrada'), ('cancelada', 'Cancelada')], default='borrador', max_length=20)),
                ('aporte_inicial', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('capital_activo', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('capital_recuperado', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('tasa_proyectada_anual', models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ('fecha_inicio', models.DateField()),
                ('fecha_cierre', models.DateField(blank=True, null=True)),
                ('descripcion', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='positions', to='gestion_creditos.investoraccount')),
            ],
            options={
                'verbose_name': 'Posicion de inversion',
                'verbose_name_plural': 'Posiciones de inversion',
                'ordering': ['-fecha_inicio', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='InvestmentReturnSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fecha_corte', models.DateField()),
                ('roi_acumulado', models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ('roi_mensual', models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ('tasa_retorno_proyectada', models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ('capital_activo', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('capital_recuperado', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('tiempo_promedio_retorno_dias', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='snapshots', to='gestion_creditos.investoraccount')),
            ],
            options={
                'verbose_name': 'Snapshot de retorno',
                'verbose_name_plural': 'Snapshots de retorno',
                'ordering': ['-fecha_corte', '-created_at'],
                'unique_together': {('account', 'fecha_corte')},
            },
        ),
        migrations.CreateModel(
            name='MarketplaceDireccionEntrega',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre_contacto', models.CharField(max_length=160)),
                ('telefono_contacto', models.CharField(max_length=20)),
                ('direccion_linea_1', models.CharField(max_length=255)),
                ('direccion_linea_2', models.CharField(blank=True, max_length=255)),
                ('ciudad', models.CharField(max_length=120)),
                ('departamento', models.CharField(blank=True, max_length=120)),
                ('referencia', models.CharField(blank=True, max_length=255)),
                ('instrucciones', models.TextField(blank=True)),
                ('pedido', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='direccion_entrega', to='gestion_creditos.marketplacepedido')),
            ],
            options={
                'verbose_name': 'Direccion de entrega marketplace',
                'verbose_name_plural': 'Direcciones de entrega marketplace',
            },
        ),
        migrations.CreateModel(
            name='MarketplaceLiquidacionEmpresa',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('estado', models.CharField(choices=[('pendiente', 'Pendiente'), ('programada', 'Programada'), ('pagada', 'Pagada'), ('conciliada', 'Conciliada'), ('manual', 'Manual')], default='pendiente', max_length=20)),
                ('valor_bruto', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('marketplace_fee_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('valor_neto', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('external_reference', models.CharField(blank=True, max_length=120)),
                ('programmed_for', models.DateField(blank=True, null=True)),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('notas', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='marketplace_liquidaciones', to='gestion_creditos.empresa')),
                ('pedido', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='liquidacion_empresa', to='gestion_creditos.marketplacepedido')),
            ],
            options={
                'verbose_name': 'Liquidacion marketplace',
                'verbose_name_plural': 'Liquidaciones marketplace',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='MarketplacePago',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('proveedor', models.CharField(choices=[('mercado_pago', 'Mercado Pago'), ('wompi', 'Wompi'), ('otro', 'Otro')], default='mercado_pago', max_length=20)),
                ('estado', models.CharField(choices=[('creado', 'Creado'), ('pendiente', 'Pendiente'), ('aprobado', 'Aprobado'), ('rechazado', 'Rechazado'), ('cancelado', 'Cancelado')], default='creado', max_length=20)),
                ('provider_payment_id', models.CharField(blank=True, max_length=120)),
                ('provider_preference_id', models.CharField(blank=True, max_length=120)),
                ('init_point_url', models.URLField(blank=True, max_length=500)),
                ('external_reference', models.CharField(blank=True, max_length=120)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('amount_gross', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('marketplace_fee_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('amount_net', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('pedido', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='pago', to='gestion_creditos.marketplacepedido')),
            ],
            options={
                'verbose_name': 'Pago marketplace',
                'verbose_name_plural': 'Pagos marketplace',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='InvestmentCashflow',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('aporte', 'Aporte'), ('retorno', 'Retorno'), ('comision', 'Comision'), ('ajuste', 'Ajuste'), ('salida_capital', 'Salida de capital')], max_length=20)),
                ('monto', models.DecimalField(decimal_places=2, max_digits=12)),
                ('fecha_efectiva', models.DateField()),
                ('descripcion', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('position', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cashflows', to='gestion_creditos.investmentposition')),
            ],
            options={
                'verbose_name': 'Movimiento de inversion',
                'verbose_name_plural': 'Movimientos de inversion',
                'ordering': ['-fecha_efectiva', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='MarketplacePedidoItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('titulo_snapshot', models.CharField(max_length=120)),
                ('tipo_snapshot', models.CharField(choices=[('producto', 'Producto'), ('servicio', 'Servicio'), ('publicidad', 'Publicidad')], max_length=20)),
                ('cantidad', models.PositiveIntegerField(default=1)),
                ('precio_unitario', models.DecimalField(decimal_places=2, max_digits=12)),
                ('total_linea', models.DecimalField(decimal_places=2, max_digits=12)),
                ('item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pedido_items', to='gestion_creditos.marketplaceitem')),
                ('pedido', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='gestion_creditos.marketplacepedido')),
            ],
            options={
                'verbose_name': 'Item de pedido marketplace',
                'verbose_name_plural': 'Items de pedido marketplace',
            },
        ),
        migrations.CreateModel(
            name='InvestmentEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('titulo', models.CharField(max_length=120)),
                ('descripcion', models.TextField(blank=True)),
                ('fecha_evento', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='gestion_creditos.investoraccount')),
                ('position', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='events', to='gestion_creditos.investmentposition')),
            ],
            options={
                'verbose_name': 'Evento de inversion',
                'verbose_name_plural': 'Eventos de inversion',
                'ordering': ['-fecha_evento'],
            },
        ),
    ]
