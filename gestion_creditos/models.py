from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator, FileExtensionValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify
from decimal import Decimal
import uuid

from gestion_creditos.services.name_normalization import build_full_name_upper, normalize_name_upper

class AsesorComercial(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asesor_comercial',
    )
    nombre = models.CharField(max_length=160)
    cedula = models.CharField(max_length=20, unique=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    activo = models.BooleanField(default=True)
    observaciones = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'Ejecutivo'
        verbose_name_plural = 'Ejecutivos'
        indexes = [
            models.Index(fields=['activo', 'nombre'], name='asesor_activo_nombre_idx'),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.cedula})"

    def save(self, *args, **kwargs):
        self.nombre = normalize_name_upper(self.nombre)
        self.cedula = (self.cedula or '').strip()
        super().save(*args, **kwargs)


class PagoComisionEjecutivo(models.Model):
    asesor = models.ForeignKey(
        AsesorComercial,
        on_delete=models.PROTECT,
        related_name='pagos_comision',
    )
    monto = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    fecha_pago = models.DateField(default=timezone.localdate)
    referencia = models.CharField(max_length=120, blank=True)
    observacion = models.TextField(blank=True)
    comprobante = models.FileField(
        upload_to='comisiones_ejecutivos/%Y/%m/',
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png', 'webp'])],
        blank=True,
        null=True,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_comision_ejecutivo_creados',
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_pago', '-creado_en']
        verbose_name = 'Pago de comision de ejecutivo'
        verbose_name_plural = 'Pagos de comision de ejecutivos'
        indexes = [
            models.Index(fields=['asesor', '-fecha_pago'], name='pago_com_eje_asesor_fecha_idx'),
        ]

    def __str__(self):
        return f"{self.asesor.nombre} - ${self.monto:,.2f} ({self.fecha_pago})"


#? Modelo movido de credito_libranza (la idea es crear las empresas directamente desde el admin)
class Empresa(models.Model):
    class TipoEmpresa(models.TextChoices):
        CONVENIO = 'CONVENIO', 'Convenio'
        MARKETPLACE_EXTERNA = 'MARKETPLACE_EXTERNA', 'Marketplace externa'
        MIXTA = 'MIXTA', 'Mixta'

    nombre = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    descripcion_marketplace = models.TextField(blank=True)
    whatsapp_contacto = models.CharField(max_length=20, blank=True)
    logo = models.ImageField(
        upload_to='marketplace/logos/',
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp', 'svg'])],
        blank=True,
        null=True
    )
    convenio_activo = models.BooleanField(default=False)
    tipo_empresa = models.CharField(
        max_length=24,
        choices=TipoEmpresa.choices,
        default=TipoEmpresa.CONVENIO,
    )
    razon_social = models.CharField(max_length=160, blank=True)
    nit = models.CharField(max_length=30, blank=True)
    representante_legal = models.CharField(max_length=160, blank=True)
    correo_contacto = models.EmailField(blank=True)
    telefono_contacto = models.CharField(max_length=20, blank=True)
    asesor_comercial = models.ForeignKey(
        AsesorComercial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='empresas_referidas'
    )
    mp_user_id = models.CharField(max_length=80, blank=True)
    mp_access_token = models.CharField(max_length=255, blank=True)
    marketplace_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('10.00'))
    pagos_habilitados = models.BooleanField(default=False)
    requiere_doble_aprobacion_libranza = models.BooleanField(
        default=False,
        verbose_name='Requiere doble aprobacion libranza',
    )
    requiere_aprobadores_distintos_libranza = models.BooleanField(
        default=True,
        verbose_name='Requiere aprobadores distintos en libranza',
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.nombre)
            slug = base_slug
            counter = 2
            while Empresa.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self._validate_logo_field(self.logo, 'logo')

    def _validate_logo_field(self, field, field_name):
        if not field:
            return

        max_logo_bytes = 2 * 1024 * 1024
        if field.size > max_logo_bytes:
            raise ValidationError({field_name: 'El logo no debe superar 2 MB.'})

        filename = (getattr(field, 'name', '') or '').lower()
        if filename.endswith('.svg'):
            return

        try:
            from PIL import Image

            image = Image.open(field)
            width, height = image.size
            field.seek(0)
        except Exception as exc:
            raise ValidationError({field_name: 'No pudimos procesar el logo. Usa una imagen valida.'}) from exc

        if width < 120 or height < 120:
            raise ValidationError({field_name: 'El logo debe tener al menos 120x120 px.'})
        if width > 2400 or height > 2400:
            raise ValidationError({field_name: 'El logo no debe superar 2400x2400 px.'})

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    @property
    def fue_referida(self):
        return self.asesor_comercial_id is not None

    @property
    def permite_libranza(self):
        return self.tipo_empresa in {self.TipoEmpresa.CONVENIO, self.TipoEmpresa.MIXTA} and self.convenio_activo

    @property
    def permite_marketplace(self):
        return self.tipo_empresa in {self.TipoEmpresa.MARKETPLACE_EXTERNA, self.TipoEmpresa.MIXTA}


class MarketplaceItem(models.Model):
    class TipoItem(models.TextChoices):
        PRODUCTO = 'producto', 'Producto'
        SERVICIO = 'servicio', 'Servicio'
        PUBLICIDAD = 'publicidad', 'Publicidad'

    class EstadoItem(models.TextChoices):
        PENDIENTE = 'pendiente', 'Pendiente'
        APROBADO = 'aprobado', 'Aprobado'
        RECHAZADO = 'rechazado', 'Rechazado'
        INACTIVO = 'inactivo', 'Inactivo'

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='marketplace_items')
    titulo = models.CharField(max_length=120)
    descripcion = models.TextField()
    beneficio = models.CharField(max_length=180)
    tipo = models.CharField(max_length=20, choices=TipoItem.choices)
    precio = models.CharField(max_length=60, blank=True)
    imagen = models.ImageField(
        upload_to='marketplace/items/',
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])],
        blank=True,
        null=True
    )
    video = models.FileField(
        upload_to='marketplace/videos/',
        validators=[FileExtensionValidator(['mp4', 'webm'])],
        blank=True,
        null=True
    )
    whatsapp_contacto = models.CharField(max_length=20, blank=True)
    estado = models.CharField(max_length=20, choices=EstadoItem.choices, default=EstadoItem.PENDIENTE)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_publicacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-fecha_creacion']

    def __str__(self):
        return f"{self.titulo} ({self.empresa.nombre})"

class MarketplaceItemHistorialEstado(models.Model):
    class OrigenCambio(models.TextChoices):
        EMPRESA = 'empresa', 'Empresa'
        ADMIN = 'admin', 'Administrador'
        SISTEMA = 'sistema', 'Sistema'

    item = models.ForeignKey(
        MarketplaceItem,
        on_delete=models.CASCADE,
        related_name='historial_estados'
    )
    estado_anterior = models.CharField(
        max_length=20,
        choices=MarketplaceItem.EstadoItem.choices,
        blank=True
    )
    estado_nuevo = models.CharField(
        max_length=20,
        choices=MarketplaceItem.EstadoItem.choices
    )
    origen = models.CharField(
        max_length=20,
        choices=OrigenCambio.choices,
        default=OrigenCambio.SISTEMA
    )
    comentario = models.CharField(max_length=255, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketplace_cambios_estado'
    )
    fecha_cambio = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_cambio']
        verbose_name = 'Historial de estado marketplace'
        verbose_name_plural = 'Historial de estados marketplace'
        indexes = [
            models.Index(fields=['item', '-fecha_cambio'], name='mk_hist_item_fecha_idx'),
            models.Index(fields=['item', 'estado_nuevo'], name='mk_hist_item_estado_idx'),
        ]

    def __str__(self):
        return f"{self.item.titulo}: {self.estado_anterior or '-'} -> {self.estado_nuevo}"


class MarketplacePedido(models.Model):
    class EstadoPedido(models.TextChoices):
        BORRADOR = 'borrador', 'Borrador'
        PENDIENTE_PAGO = 'pendiente_pago', 'Pendiente de pago'
        PAGADO = 'pagado', 'Pagado'
        EN_GESTION = 'en_gestion', 'En gestion'
        COMPLETADO = 'completado', 'Completado'
        CANCELADO = 'cancelado', 'Cancelado'

    numero_pedido = models.CharField(max_length=30, unique=True, editable=False)
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name='marketplace_pedidos')
    comprador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketplace_pedidos'
    )
    comprador_nombre = models.CharField(max_length=160)
    comprador_email = models.EmailField()
    comprador_telefono = models.CharField(max_length=20, blank=True)
    estado = models.CharField(max_length=20, choices=EstadoPedido.choices, default=EstadoPedido.BORRADOR)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    marketplace_fee_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    moneda = models.CharField(max_length=10, default='COP')
    external_reference = models.CharField(max_length=100, blank=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Pedido marketplace'
        verbose_name_plural = 'Pedidos marketplace'

    def save(self, *args, **kwargs):
        if not self.numero_pedido:
            ultimo = MarketplacePedido.objects.order_by('-id').first()
            numero = (ultimo.id + 1) if ultimo else 1
            self.numero_pedido = f"MKP-{numero:06d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero_pedido


class MarketplaceDireccionEntrega(models.Model):
    pedido = models.OneToOneField(
        MarketplacePedido,
        on_delete=models.CASCADE,
        related_name='direccion_entrega'
    )
    nombre_contacto = models.CharField(max_length=160)
    telefono_contacto = models.CharField(max_length=20)
    direccion_linea_1 = models.CharField(max_length=255)
    direccion_linea_2 = models.CharField(max_length=255, blank=True)
    ciudad = models.CharField(max_length=120)
    departamento = models.CharField(max_length=120, blank=True)
    referencia = models.CharField(max_length=255, blank=True)
    instrucciones = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Direccion de entrega marketplace'
        verbose_name_plural = 'Direcciones de entrega marketplace'

    def __str__(self):
        return f"{self.pedido.numero_pedido} - {self.ciudad}"


class MarketplacePedidoItem(models.Model):
    pedido = models.ForeignKey(
        MarketplacePedido,
        on_delete=models.CASCADE,
        related_name='items'
    )
    item = models.ForeignKey(
        MarketplaceItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pedido_items'
    )
    titulo_snapshot = models.CharField(max_length=120)
    tipo_snapshot = models.CharField(max_length=20, choices=MarketplaceItem.TipoItem.choices)
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    total_linea = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = 'Item de pedido marketplace'
        verbose_name_plural = 'Items de pedido marketplace'

    def __str__(self):
        return f"{self.pedido.numero_pedido} - {self.titulo_snapshot}"


class MarketplacePago(models.Model):
    class ProveedorPago(models.TextChoices):
        MERCADO_PAGO = 'mercado_pago', 'Mercado Pago'
        WOMPI = 'wompi', 'Wompi'
        OTRO = 'otro', 'Otro'

    class EstadoPago(models.TextChoices):
        CREADO = 'creado', 'Creado'
        PENDIENTE = 'pendiente', 'Pendiente'
        APROBADO = 'aprobado', 'Aprobado'
        RECHAZADO = 'rechazado', 'Rechazado'
        CANCELADO = 'cancelado', 'Cancelado'

    pedido = models.OneToOneField(
        MarketplacePedido,
        on_delete=models.CASCADE,
        related_name='pago'
    )
    proveedor = models.CharField(max_length=20, choices=ProveedorPago.choices, default=ProveedorPago.MERCADO_PAGO)
    estado = models.CharField(max_length=20, choices=EstadoPago.choices, default=EstadoPago.CREADO)
    provider_payment_id = models.CharField(max_length=120, blank=True)
    provider_preference_id = models.CharField(max_length=120, blank=True)
    init_point_url = models.URLField(max_length=500, blank=True)
    external_reference = models.CharField(max_length=120, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    amount_gross = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    marketplace_fee_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_net = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Pago marketplace'
        verbose_name_plural = 'Pagos marketplace'

    def __str__(self):
        return f"{self.pedido.numero_pedido} - {self.proveedor}"


class MarketplaceLiquidacionEmpresa(models.Model):
    class EstadoLiquidacion(models.TextChoices):
        PENDIENTE = 'pendiente', 'Pendiente'
        PROGRAMADA = 'programada', 'Programada'
        PAGADA = 'pagada', 'Pagada'
        CONCILIADA = 'conciliada', 'Conciliada'
        MANUAL = 'manual', 'Manual'

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name='marketplace_liquidaciones')
    pedido = models.OneToOneField(
        MarketplacePedido,
        on_delete=models.CASCADE,
        related_name='liquidacion_empresa'
    )
    estado = models.CharField(max_length=20, choices=EstadoLiquidacion.choices, default=EstadoLiquidacion.PENDIENTE)
    valor_bruto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    marketplace_fee_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_neto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    external_reference = models.CharField(max_length=120, blank=True)
    programmed_for = models.DateField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Liquidacion marketplace'
        verbose_name_plural = 'Liquidaciones marketplace'

    def __str__(self):
        return f"{self.empresa.nombre} - {self.pedido.numero_pedido}"

#? ----- Modelo principal de crédito ----
class Credito(models.Model):
    class LineaCredito(models.TextChoices):
        EMPRENDIMIENTO = 'EMPRENDIMIENTO', 'Emprendimiento'
        LIBRANZA = 'LIBRANZA', 'Libranza'
        ADELANTO_NOMINA = 'ADELANTO_NOMINA', 'Adelanto de Nomina'

    class TipoReglaCredito(models.TextChoices):
        NORMAL = 'NORMAL', 'Normal'
        ESPECIAL = 'ESPECIAL', 'Especial'

    class EstadoCredito(models.TextChoices):
        SOLICITUD = 'SOLICITUD', 'Solicitud'
        PENDIENTE_APROBACION_FINAL = 'PENDIENTE_APROBACION_FINAL', 'Pendiente aprobacion final'
        EN_REVISION = 'EN_REVISION', 'En Revisión'
        APROBADO_PAGADOR = 'APROBADO_PAGADOR', 'Aprobado por Pagador'
        APROBADO = 'APROBADO', 'Aprobado'
        RECHAZADO = 'RECHAZADO', 'Rechazado'
        PENDIENTE_FIRMA = 'PENDIENTE_FIRMA', 'Pendiente Firma'
        FIRMADO = 'FIRMADO', 'Firmado'
        PENDIENTE_TRANSFERENCIA = 'PENDIENTE_TRANSFERENCIA', 'Pendiente por Transferencia'
        ACTIVO = 'ACTIVO', 'Activo'
        EN_MORA = 'EN_MORA', 'En Mora'
        PAGADO = 'PAGADO', 'Pagado'

    # Campos comunes a todos los créditos
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='creditos')
    numero_credito = models.CharField(max_length=20, unique=True, editable=False, help_text="ID único y legible para el crédito (ej. CR-2024-00001)")
    linea = models.CharField(max_length=30, choices=LineaCredito.choices)
    estado = models.CharField(max_length=30, choices=EstadoCredito.choices, default=EstadoCredito.SOLICITUD)
    documento_enviado = models.BooleanField(default=False, help_text="Indica si el pagaré ha sido enviado para firma.")
    
    # --- Campos financieros (ÚNICA FUENTE DE VERDAD) ---
    monto_solicitado = models.DecimalField(
        max_digits=14, 
        decimal_places=2, 
        help_text="Monto solicitado por el cliente"
    )
    plazo_solicitado = models.IntegerField(
        help_text="Plazo solicitado en meses"
    )
    monto_aprobado = models.DecimalField(
        max_digits=14, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Monto aprobado por el analista (puede diferir del solicitado)"
    )
    plazo = models.IntegerField(
        null=True, 
        blank=True, 
        help_text="Plazo aprobado en meses"
    )
    tasa_interes = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        null=True, 
        blank=True, 
        help_text="Tasa de interés mensual (%)"
    )
    comision = models.DecimalField(
        max_digits=14, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Comisión de estudio del crédito"
    )
    iva_comision = models.DecimalField(
        max_digits=14, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="IVA sobre la comisión (19%)"
    )
    total_a_pagar = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Total a pagar (capital + intereses + comisión + IVA)"
    )
    saldo_pendiente = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Saldo pendiente por pagar"
    )
    capital_pendiente = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Capital pendiente por amortizar (sin intereses)"
    )
    valor_cuota = models.DecimalField(
        max_digits=14, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Valor de la cuota fija mensual"
    )
    fecha_proximo_pago = models.DateField(
        null=True, 
        blank=True,
        help_text="Fecha de vencimiento de la próxima cuota"
    )
    fecha_desembolso = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en la que se realizó el desembolso"
    )
    
    tipo_regla_credito = models.CharField(
        max_length=20,
        choices=TipoReglaCredito.choices,
        default=TipoReglaCredito.NORMAL,
        help_text="Permite modelar creditos especiales sin excepciones invisibles."
    )
    fecha_primera_cuota_forzada = models.DateField(
        null=True,
        blank=True,
        help_text="Fecha manual de primera cuota para creditos especiales."
    )
    plazo_forzado = models.IntegerField(
        null=True,
        blank=True,
        help_text="Plazo aplicado por regla especial, si difiere del aprobado."
    )
    tasa_forzada = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Tasa mensual aplicada por regla especial."
    )
    observacion_regla_especial = models.TextField(
        blank=True,
        help_text="Justificacion operativa de la regla especial."
    )
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        """
        Método save unificado que:
        1. Genera el numero_credito si no existe
        2. Valida las transiciones de estado permitidas
        """
        # 1. Generar numero_credito si es un crédito nuevo
        if not self.numero_credito:
            from django.utils import timezone
            today = timezone.now()
            year = today.year
            
            # Generar el prefijo y buscar el último número para ese año
            prefix = f'CR-{year}-'
            last_credit = Credito.objects.filter(numero_credito__startswith=prefix).order_by('numero_credito').last()
            
            if last_credit and last_credit.numero_credito[len(prefix):].isdigit():
                last_sequence = int(last_credit.numero_credito[len(prefix):])
                new_sequence = last_sequence + 1
            else:
                new_sequence = 1
            
            self.numero_credito = f'{prefix}{new_sequence:05d}'

        # 2. Validación de transiciones de estado
        if self.pk:  # Si el objeto ya existe en la BD
            try:
                credito_anterior = Credito.objects.get(pk=self.pk)
                
                # Validar que un crédito ACTIVO solo pueda cambiar a EN_MORA o PAGADO
                if (credito_anterior.estado == self.EstadoCredito.ACTIVO and 
                    self.estado not in [self.EstadoCredito.ACTIVO, self.EstadoCredito.EN_MORA, self.EstadoCredito.PAGADO]):
                    raise ValidationError(
                        f'Un crédito en estado "Activo" no puede cambiar a "{self.get_estado_display()}". '
                        f'Solo se permiten las transiciones: Activo → En Mora o Activo → Pagado.'
                    )
                
                # Validar que un crédito PAGADO no pueda cambiar de estado
                if credito_anterior.estado == self.EstadoCredito.PAGADO:
                    raise ValidationError(
                        'Un crédito en estado "Pagado" no puede cambiar de estado.'
                    )
                    
            except Credito.DoesNotExist:
                pass  # El objeto es nuevo, no hay estado anterior para comparar

        super(Credito, self).save(*args, **kwargs)

    class Meta:
        ordering = ['-fecha_solicitud']
        permissions = [
            (
                'can_originate_special_libranza',
                'Puede simular y originar casos especiales de libranza',
            ),
        ]
        verbose_name = 'Crédito'
        verbose_name_plural = 'Créditos'
        indexes = [
            # Índices para filtros frecuentes en dashboards y listados
            models.Index(fields=['estado'], name='idx_credito_estado'),
            models.Index(fields=['linea'], name='idx_credito_linea'),
            models.Index(fields=['estado', 'linea'], name='idx_credito_estado_linea'),

            # Índice para detectar créditos en mora (tarea automática)
            models.Index(fields=['fecha_proximo_pago'], name='idx_credito_fecha_pago'),
            models.Index(fields=['estado', 'fecha_proximo_pago'], name='idx_credito_estado_fecha'),

            # Índice para ordenamiento por defecto y búsquedas por fecha
            models.Index(fields=['-fecha_solicitud'], name='idx_credito_fecha_sol'),

            # Índice para búsquedas por usuario
            models.Index(fields=['usuario', 'estado'], name='idx_credito_usuario_estado'),

            # Índice para búsquedas por número de crédito (aunque ya es unique, ayuda en JOINs)
            models.Index(fields=['numero_credito'], name='idx_credito_numero'),
        ]

    def __str__(self):
        return f'{self.get_linea_display()} {self.numero_credito} - {self.usuario.username}'

    @property
    def nombre_cliente(self):
        """
        Devuelve el nombre completo del cliente desde el detalle del crédito.
        """
        if self.detalle:
            if self.linea == self.LineaCredito.EMPRENDIMIENTO and hasattr(self.detalle, 'nombre'):
                return normalize_name_upper(self.detalle.nombre)
            elif self.linea == self.LineaCredito.LIBRANZA and hasattr(self.detalle, 'nombre_completo'):
                return normalize_name_upper(self.detalle.nombre_completo)
            elif self.linea == self.LineaCredito.ADELANTO_NOMINA and hasattr(self.detalle, 'nombre_cliente'):
                return normalize_name_upper(self.detalle.nombre_cliente)
        # Fallback por si el detalle no está o por alguna razón no tiene nombre
        return normalize_name_upper(self.usuario.get_full_name() or self.usuario.username)

    @property
    def detalle(self):
        """
        Devuelve la instancia del modelo de detalle específico (Libranza o Emprendimiento)
        basado en la línea de crédito.
        """
        if self.linea == self.LineaCredito.LIBRANZA:
            return getattr(self, 'detalle_libranza', None)
        elif self.linea == self.LineaCredito.EMPRENDIMIENTO:
            return getattr(self, 'detalle_emprendimiento', None)
        elif self.linea == self.LineaCredito.ADELANTO_NOMINA:
            return getattr(self, 'detalle_adelanto_nomina', None)
        return None

    @property
    def cliente_documento(self):
        detalle = self.detalle
        if not detalle:
            return ''
        if self.linea == self.LineaCredito.LIBRANZA:
            return getattr(detalle, 'cedula', '')
        if self.linea == self.LineaCredito.EMPRENDIMIENTO:
            return getattr(detalle, 'numero_cedula', '')
        if self.linea == self.LineaCredito.ADELANTO_NOMINA:
            return getattr(detalle.vinculo_laboral, 'documento_empleado', '')
        return ''

    @property
    def cliente_email(self):
        detalle = self.detalle
        if self.linea == self.LineaCredito.LIBRANZA and detalle:
            return getattr(detalle, 'correo_electronico', '') or self.usuario.email
        if self.linea == self.LineaCredito.ADELANTO_NOMINA and detalle:
            return getattr(detalle.vinculo_laboral, 'correo_empleado', '') or self.usuario.email
        return self.usuario.email

    @property
    def cliente_telefono(self):
        detalle = self.detalle
        if self.linea == self.LineaCredito.LIBRANZA and detalle:
            return getattr(detalle, 'telefono', '')
        if self.linea == self.LineaCredito.EMPRENDIMIENTO and detalle:
            return getattr(detalle, 'celular_wh', '')
        if self.linea == self.LineaCredito.ADELANTO_NOMINA and detalle:
            return getattr(detalle.vinculo_laboral, 'telefono_empleado', '')
        return ''

    @property
    def empresa_relacionada(self):
        detalle = self.detalle
        if self.linea == self.LineaCredito.LIBRANZA and detalle:
            return getattr(detalle, 'empresa', None)
        if self.linea == self.LineaCredito.ADELANTO_NOMINA and detalle:
            return getattr(detalle.vinculo_laboral, 'empresa', None)
        return None

    @property
    def es_linea_libranza_operativa(self):
        return self.linea in {
            self.LineaCredito.LIBRANZA,
            self.LineaCredito.ADELANTO_NOMINA,
        }

    @property
    def dias_en_mora(self):
        """
        Calcula los días en mora basado en la fecha del próximo pago.
        """
        from django.utils import timezone
        if self.estado == self.EstadoCredito.EN_MORA and self.fecha_proximo_pago:
            dias = (timezone.now().date() - self.fecha_proximo_pago).days
            return dias if dias > 0 else 0
        return 0

    @property
    def capital_pagado(self):
        """
        Calcula el monto de capital que ya ha sido pagado.
        """
        if self.monto_aprobado is None or self.capital_pendiente is None:
            return 0
        
        # Se asume que capital_pendiente es el capital que aún se debe del monto original aprobado
        pagado = self.monto_aprobado - self.capital_pendiente
        return max(0, pagado)

    @property
    def capital_financiado(self):
        """
        Calcula el capital total financiado (sin intereses).

        Returns:
            Decimal: Monto aprobado + comisión + IVA

        Ejemplo:
            Monto: $1,000,000
            Comisión (10%): $100,000
            IVA (19% sobre comisión): $19,000
            Capital Financiado: $1,119,000
        """
        if not self.monto_aprobado:
            return 0

        comision = self.comision or 0
        iva = self.iva_comision or 0

        return self.monto_aprobado + comision + iva

    @property
    def porcentaje_pagado(self):
        """
        Calcula el porcentaje del capital aprobado que ha sido pagado.
        """
        if not self.monto_aprobado or self.monto_aprobado == 0:
            return 0

        # Usa la nueva propiedad capital_pagado para el cálculo
        porcentaje = (self.capital_pagado / self.monto_aprobado) * 100

        return round(max(0, min(100, float(porcentaje))), 2)


#? ----- Modelo de crédito de emprendimiento -----
class CreditoEmprendimiento(models.Model):
    """
    Modelo para créditos de emprendimiento.
    SOLO contiene información de la SOLICITUD INICIAL.
    Los datos financieros están en el modelo Credito principal.
    """
    #! --- Relación con el Crédito Principal ---
    credito = models.OneToOneField(Credito, on_delete=models.CASCADE, related_name='detalle_emprendimiento')

    #! --- Campos de la Solicitud ÚNICAMENTE ---
    nombre = models.CharField(max_length=100, verbose_name="Nombre completo del solicitante")
    numero_cedula = models.CharField(max_length=20, verbose_name="Número de cédula")
    fecha_nac = models.DateField(verbose_name="Fecha de nacimiento")
    celular_wh = models.CharField(max_length=20, verbose_name="Celular/WhatsApp")
    direccion = models.TextField(verbose_name="Dirección de residencia")
    estado_civil = models.CharField(max_length=20, verbose_name="Estado civil")
    numero_personas_cargo = models.IntegerField(verbose_name="Número de personas a cargo")
    
    # Información del negocio
    nombre_negocio = models.CharField(max_length=100, verbose_name="Nombre del negocio")
    ubicacion_negocio = models.TextField(verbose_name="Ubicación del negocio")
    tiempo_operando = models.CharField(max_length=50, verbose_name="Tiempo operando")
    dias_trabajados_sem = models.IntegerField(verbose_name="Días trabajados por semana")
    prod_serv_ofrec = models.TextField(verbose_name="Productos/servicios que ofrece")
    ingresos_prom_mes = models.CharField(max_length=50, verbose_name="Ingresos promedio mensuales")
    cli_aten_day = models.IntegerField(verbose_name="Clientes atendidos por día")
    inventario = models.CharField(max_length=2, verbose_name="¿Tiene inventario?")
    
    # Referencias
    nomb_ref_per1 = models.CharField(max_length=100, verbose_name="Nombre referencia personal 1")
    cel_ref_per1 = models.CharField(max_length=20, verbose_name="Celular referencia personal 1")
    rel_ref_per1 = models.CharField(max_length=50, verbose_name="Relación referencia personal 1")
    nomb_ref_cl1 = models.CharField(max_length=100, verbose_name="Nombre referencia cliente 1")
    cel_ref_cl1 = models.CharField(max_length=20, verbose_name="Celular referencia cliente 1")
    rel_ref_cl1 = models.CharField(max_length=50, verbose_name="Relación referencia cliente 1")
    ref_conoc_lid_com = models.CharField(max_length=2, verbose_name="¿Conoce al líder comunitario?")
    
    # Archivos adjuntos
    foto_negocio = models.FileField(
        upload_to='fotos_negocios/',
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        help_text="Solo se permiten archivos PDF"
    )
    desc_fotos_neg = models.TextField(verbose_name="Descripción de las fotos del negocio")
    
    # Información adicional
    tipo_cta_mno = models.CharField(max_length=20, verbose_name="Tipo de cuenta de mano")
    ahorro_tand_alc = models.CharField(max_length=2, verbose_name="¿Tiene ahorro en tanda/alcancía?")
    depend_h = models.CharField(max_length=2, verbose_name="¿Tiene dependientes?")
    desc_cred_nec = models.TextField(verbose_name="Descripción de por qué necesita el crédito")
    redes_soc = models.CharField(max_length=2, verbose_name="¿Tiene redes sociales?")
    fotos_prod = models.CharField(max_length=2, verbose_name="¿Tiene fotos de productos?")

    # --- Evaluación del Administrador ---
    puntaje = models.IntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Puntaje de evaluación del crédito (0-100)"
    )
    observaciones_analista = models.TextField(
        blank=True,
        null=True,
        help_text="Observaciones del analista durante la evaluación"
    )

    # --- Scoring de Imágenes con IA ---
    puntaje_imagenes = models.FloatField(
        default=0.0,
        help_text="Puntaje obtenido del análisis de imágenes con IA (0-18)"
    )
    datos_scoring_imagenes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Datos completos del scoring de imágenes"
    )

    class Meta:
        verbose_name = 'Detalle de Emprendimiento'
        verbose_name_plural = 'Detalles de Emprendimiento'

    def __str__(self):
        return f"Detalle Emprendimiento - {self.nombre} ({self.credito.numero_credito})"


#? ----- Modelo para múltiples imágenes del negocio -----
class ImagenNegocio(models.Model):
    """
    Modelo para almacenar las múltiples imágenes del negocio.
    Permite al sistema de scoring IA analizar diferentes aspectos del negocio.
    """
    TIPO_IMAGEN_CHOICES = [
        ('building_exterior', 'Exterior del Local/Edificio'),
        ('room_interior', 'Interior del Espacio'),
        ('products_display', 'Productos en Exhibición'),
        ('shelves_storage', 'Estantes/Almacenamiento'),
        ('general_business', 'Vista General del Negocio'),
    ]

    credito_emprendimiento = models.ForeignKey(
        CreditoEmprendimiento,
        on_delete=models.CASCADE,
        related_name='imagenes_negocio'
    )
    imagen = models.ImageField(
        upload_to='imagenes_negocios/%Y/%m/%d/',
        help_text="Imágenes del negocio (JPG, PNG, WEBP)"
    )
    tipo_imagen = models.CharField(
        max_length=20,
        choices=TIPO_IMAGEN_CHOICES,
        default='general_business',
        verbose_name="Tipo de imagen"
    )
    descripcion = models.TextField(
        blank=True,
        help_text="Descripción opcional de la imagen"
    )
    fecha_subida = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha_subida']
        verbose_name = 'Imagen del Negocio'
        verbose_name_plural = 'Imágenes del Negocio'

    def __str__(self):
        return f"Imagen {self.get_tipo_imagen_display()} - {self.credito_emprendimiento.nombre_negocio}"


#? ----- Modelo de crédito de libranza -----
class CreditoLibranza(models.Model):
    """
    Modelo para créditos de libranza.
    SOLO contiene información de la SOLICITUD INICIAL.
    Los datos financieros están en el modelo Credito principal.
    """
    #! --- Relación con el Crédito Principal ---
    credito = models.OneToOneField(Credito, on_delete=models.CASCADE, related_name='detalle_libranza')

    #? Información personal del solicitante
    nombres = models.CharField(max_length=100, verbose_name="Nombres")
    apellidos = models.CharField(max_length=100, verbose_name="Apellidos")
    cedula = models.CharField(max_length=20, verbose_name="Numero de cedula")
    direccion = models.CharField(max_length=255, verbose_name="Dirección de residencia")
    telefono = models.CharField(max_length=20, verbose_name="Teléfono de contacto")
    correo_electronico = models.EmailField(verbose_name="Correo electrónico")

    #? Información laboral
    empresa = models.ForeignKey(
        Empresa, 
        on_delete=models.CASCADE,
        verbose_name="Empresa donde labora"
    )
    ingresos_mensuales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Ingresos mensuales"
    )

    #? Archivos adjuntos
    cedula_frontal = models.FileField(
        upload_to='credito_libranza/cedulas/',
        verbose_name="Cédula (frontal)"
    )
    cedula_trasera = models.FileField(
        upload_to='credito_libranza/cedulas/',
        verbose_name="Cédula (trasera)"
    )
    certificado_laboral = models.FileField(
        upload_to='credito_libranza/certificados_laborales/',
        null=True,
        blank=True,
        verbose_name="Certificado laboral"
    )
    desprendible_nomina = models.FileField(
        upload_to='credito_libranza/desprendibles_nomina/',
        null=True,
        blank=True,
        verbose_name="Desprendible de nómina"
    )
    certificado_bancario = models.FileField(
        upload_to='credito_libranza/certificados_bancarios/',
        verbose_name="Certificado bancario"
    )
    certificado_bancario_metadata = models.JSONField(
        default=dict,
        blank=True
    )
    certificado_bancario_estado_extraccion = models.CharField(
        max_length=20,
        choices=[('pendiente', 'Pendiente'), ('completo', 'Completo'), ('error', 'Error')],
        default='pendiente'
    )
    certificado_bancario_ultima_extraccion = models.DateTimeField(
        null=True,
        blank=True
    )

    class Meta:
        verbose_name = 'Detalle de Libranza'
        verbose_name_plural = 'Detalles de Libranza'

    def __str__(self):
        return f"Detalle Libranza - {self.nombre_completo} ({self.credito.numero_credito})"

    @property
    def nombre_completo(self):
        return build_full_name_upper(self.nombres, self.apellidos)


class AprobacionPagadorLibranza(models.Model):
    class Nivel(models.TextChoices):
        NIVEL_1 = 'NIVEL_1', 'Nivel 1'
        FINAL = 'FINAL', 'Final'

    class Decision(models.TextChoices):
        APROBADO = 'APROBADO', 'Aprobado'
        RECHAZADO = 'RECHAZADO', 'Rechazado'

    credito = models.ForeignKey(
        Credito,
        on_delete=models.CASCADE,
        related_name='aprobaciones_pagador_libranza',
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name='aprobaciones_pagador_libranza',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='aprobaciones_pagador_libranza',
    )
    nivel = models.CharField(max_length=20, choices=Nivel.choices)
    decision = models.CharField(max_length=20, choices=Decision.choices)
    observacion = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Aprobacion pagador libranza'
        verbose_name_plural = 'Aprobaciones pagador libranza'
        indexes = [
            models.Index(fields=['credito', 'nivel', 'decision'], name='apr_pag_lib_cred_niv_dec_idx'),
            models.Index(fields=['empresa', '-created_at'], name='apr_pag_lib_emp_fecha_idx'),
        ]

    def __str__(self):
        return f"{self.credito.numero_credito} - {self.get_nivel_display()} - {self.get_decision_display()}"


class VinculoLaboralEmpresa(models.Model):
    class EstadoVinculo(models.TextChoices):
        ACTIVO = 'ACTIVO', 'Activo'
        INACTIVO = 'INACTIVO', 'Inactivo'
        SUSPENDIDO = 'SUSPENDIDO', 'Suspendido'

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vinculos_laborales'
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name='vinculos_laborales'
    )
    documento_empleado = models.CharField(max_length=20)
    tipo_documento = models.CharField(max_length=10, default='CC')
    nombre_empleado = models.CharField(max_length=160)
    correo_empleado = models.EmailField(blank=True)
    telefono_empleado = models.CharField(max_length=20, blank=True)
    estado_vinculo = models.CharField(
        max_length=20,
        choices=EstadoVinculo.choices,
        default=EstadoVinculo.ACTIVO
    )
    fecha_alta_aprobado = models.DateField()
    salario_base_mensual = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    auxilio_transporte_mensual = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    descuentos_fijos_mensuales = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    validado_por_pagador = models.BooleanField(default=False)
    observaciones = models.TextField(blank=True)
    cargado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vinculos_cargados',
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Vinculo laboral con empresa'
        verbose_name_plural = 'Vinculos laborales con empresas'
        ordering = ['-fecha_alta_aprobado', '-creado_en']
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'empresa'],
                name='uniq_vinculo_laboral_usuario_empresa'
            )
        ]

    def __str__(self):
        return f"{self.nombre_empleado} - {self.empresa.nombre}"

    @property
    def cumple_antiguedad_minima(self):
        from datetime import timedelta
        from django.utils import timezone
        return self.fecha_alta_aprobado <= (timezone.localdate() - timedelta(days=30))

    @property
    def ingreso_laboral_total(self):
        return (self.salario_base_mensual or Decimal('0.00')) + (self.auxilio_transporte_mensual or Decimal('0.00'))

    @property
    def ingreso_neto_estimado(self):
        neto = self.ingreso_laboral_total - (self.descuentos_fijos_mensuales or Decimal('0.00'))
        return neto if neto > 0 else Decimal('0.00')

    @property
    def adelanto_maximo(self):
        if not self.ingreso_neto_estimado:
            return Decimal('0.00')
        return (self.ingreso_neto_estimado / Decimal('30') * Decimal('5')).quantize(Decimal('0.01'))


class CreditoAdelantoNomina(models.Model):
    credito = models.OneToOneField(
        Credito,
        on_delete=models.CASCADE,
        related_name='detalle_adelanto_nomina'
    )
    vinculo_laboral = models.ForeignKey(
        VinculoLaboralEmpresa,
        on_delete=models.PROTECT,
        related_name='creditos_adelanto'
    )
    monto_solicitado = models.DecimalField(max_digits=12, decimal_places=2)
    monto_maximo_calculado = models.DecimalField(max_digits=12, decimal_places=2)
    dias_adelanto = models.PositiveSmallIntegerField(default=5)
    salario_base_usado = models.DecimalField(max_digits=12, decimal_places=2)
    motivo_bloqueo = models.CharField(max_length=255, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Detalle de adelanto de nomina'
        verbose_name_plural = 'Detalles de adelanto de nomina'

    def __str__(self):
        return f"Adelanto {self.credito.numero_credito} - {self.vinculo_laboral.nombre_empleado}"

    @property
    def nombre_cliente(self):
        return normalize_name_upper(self.vinculo_laboral.nombre_empleado)


#? ----- Modelo de lote de pagos de empresa -----
class LotePagoEmpresa(models.Model):
    class EstadoLote(models.TextChoices):
        CARGADO = 'CARGADO', 'Cargado'
        PROCESADO = 'PROCESADO', 'Procesado'
        PROCESADO_CON_ERRORES = 'PROCESADO_CON_ERRORES', 'Procesado con errores'

    empresa = models.ForeignKey('Empresa', on_delete=models.CASCADE, related_name='lotes_pago')
    archivo = models.FileField(upload_to='creditos/pagos/lotes/%Y/%m/')
    comprobante = models.FileField(
        upload_to='creditos/pagos/comprobantes/%Y/%m/',
        blank=True,
        null=True,
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png', 'webp'])],
    )
    nombre_original = models.CharField(max_length=255)
    checksum = models.CharField(max_length=64, db_index=True)
    estado = models.CharField(max_length=32, choices=EstadoLote.choices, default=EstadoLote.CARGADO)
    total_registros = models.PositiveIntegerField(default=0)
    pagos_aplicados = models.PositiveIntegerField(default=0)
    errores_count = models.PositiveIntegerField(default=0)
    notas = models.TextField(blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lotes_pago_creados',
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Lote de pago de empresa'
        verbose_name_plural = 'Lotes de pago de empresas'
        indexes = [
            models.Index(fields=['empresa', 'checksum'], name='lote_pago_emp_chk_idx'),
        ]

    def __str__(self):
        return f"Lote {self.nombre_original} - {self.empresa.nombre}"


#? ----- Modelo de historial de pagos -----
class HistorialPago(models.Model):
    """
    Historial de pagos realizados sobre un crédito.
    Cada registro representa un pago (parcial o total) realizado por el cliente.
    """
    class EstadoPago(models.TextChoices):
        EXITOSO = 'EXITOSO', 'Exitoso'
        FALLIDO = 'FALLIDO', 'Fallido'
        PENDIENTE = 'PENDIENTE', 'Pendiente'

    class MetodoPago(models.TextChoices):
        NO_DEFINIDO = 'NO_DEFINIDO', 'No definido'
        WOMPI = 'WOMPI', 'Wompi'
        TRANSFERENCIA_DIRECTA = 'TRANSFERENCIA_DIRECTA', 'Transferencia directa'
        OFFLINE_MANUAL = 'OFFLINE_MANUAL', 'Offline manual'

    class OrigenRegistro(models.TextChoices):
        LEGACY = 'LEGACY', 'Legacy'
        PASARELA_WOMPI = 'PASARELA_WOMPI', 'Pasarela Wompi'
        CARGA_MASIVA_EMPRESA = 'CARGA_MASIVA_EMPRESA', 'Carga masiva empresa'
        REGISTRO_MANUAL_ADMIN = 'REGISTRO_MANUAL_ADMIN', 'Registro manual admin'
        REGISTRO_MANUAL_PAGADOR = 'REGISTRO_MANUAL_PAGADOR', 'Registro manual pagador'

    #! Relación con el crédito principal
    credito = models.ForeignKey(Credito, on_delete=models.CASCADE, related_name='historial_pagos')
    
    # Información del pago
    fecha_pago = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de pago")
    fecha_aplicacion = models.DateTimeField(default=timezone.now, verbose_name="Fecha de aplicacion")
    monto = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        verbose_name="Monto pagado"
    )
    referencia_pago = models.CharField(
        max_length=100, 
        unique=True,
        verbose_name="Referencia de transacción"
    )
    estado = models.CharField(
        max_length=20, 
        choices=EstadoPago.choices, 
        default=EstadoPago.PENDIENTE
    )
    metodo_pago = models.CharField(
        max_length=30,
        choices=MetodoPago.choices,
        default=MetodoPago.NO_DEFINIDO,
    )
    origen_registro = models.CharField(
        max_length=32,
        choices=OrigenRegistro.choices,
        default=OrigenRegistro.LEGACY,
    )
    empresa_origen = models.ForeignKey(
        'Empresa',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_credito_registrados',
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_credito_registrados',
    )
    lote_pago = models.ForeignKey(
        'LotePagoEmpresa',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos',
    )
    wompi_intento = models.ForeignKey(
        'WompiIntent',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_historial',
    )
    comprobante = models.FileField(
        upload_to='creditos/pagos/comprobantes/%Y/%m/',
        blank=True,
        null=True,
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png', 'webp'])],
    )
    
    # Desglose del pago (calculado al momento de registrar)
    capital_abonado = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Porción del pago que abonó a capital"
    )
    intereses_pagados = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Porción del pago que cubrió intereses"
    )
    
    # Observaciones
    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name="Notas adicionales"
    )

    class Meta:
        ordering = ['-fecha_aplicacion', '-fecha_pago']
        verbose_name = 'Historial de Pago'
        verbose_name_plural = 'Historial de Pagos'

    def __str__(self):
        return f"Pago {self.referencia_pago} - ${self.monto} ({self.get_estado_display()})"


#? ----- Modelo de intentos de pago WOMPI -----
class WompiIntent(models.Model):
    """
    Registra intentos de pago generados contra WOMPI para auditoria y control de duplicados.
    """
    class Estado(models.TextChoices):
        CREATED = 'CREATED', 'Created'
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        DECLINED = 'DECLINED', 'Declined'
        ERROR = 'ERROR', 'Error'
        EXPIRED = 'EXPIRED', 'Expired'

    credito = models.ForeignKey(Credito, on_delete=models.CASCADE, related_name='wompi_intentos')
    referencia = models.CharField(max_length=100)
    amount_in_cents = models.BigIntegerField()
    payment_method = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=20, choices=Estado.choices, default=Estado.CREATED)
    wompi_transaction_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    referer = models.CharField(max_length=255, blank=True)
    attempts = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Wompi Intent'
        verbose_name_plural = 'Wompi Intents'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['credito', 'status'], name='wompi_int_c_s_idx'),
            models.Index(fields=['referencia'], name='wompi_int_ref_idx'),
        ]

    def __str__(self):
        return f"WompiIntent {self.referencia} - {self.status}"


#? ----- Modelo de historial de estados -----
class HistorialEstado(models.Model):
    """
    Guarda un registro de cada cambio de estado de un crédito.
    Útil para auditoría y trazabilidad.
    """
    credito = models.ForeignKey(Credito, on_delete=models.CASCADE, related_name='historial_estados')
    estado_anterior = models.CharField(
        max_length=30, 
        choices=Credito.EstadoCredito.choices, 
        null=True, 
        blank=True
    )
    estado_nuevo = models.CharField(
        max_length=30, 
        choices=Credito.EstadoCredito.choices
    )
    fecha = models.DateTimeField(auto_now_add=True)
    usuario_modificacion = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Usuario que realizó el cambio"
    )
    motivo = models.TextField(
        blank=True, 
        null=True, 
        help_text="Razón o motivo del cambio de estado"
    )
    comprobante_pago = models.FileField(
        upload_to='comprobantes_pago/', 
        blank=True, 
        null=True
    )

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'Historial de Estado'
        verbose_name_plural = 'Historial de Estados'

    def __str__(self):
        estado_ant = self.estado_anterior or 'Inicial'
        return f"{self.credito.numero_credito}: {estado_ant} → {self.estado_nuevo}"


#? ----- Modelo de cuota de amortización -----
class CuotaAmortizacion(models.Model):
    """
    Representa una única cuota en la tabla de amortización de un crédito.
    Se genera automáticamente cuando un crédito es aprobado.
    """
    credito = models.ForeignKey(
        Credito, 
        on_delete=models.CASCADE, 
        related_name='tabla_amortizacion'
    )
    numero_cuota = models.IntegerField(verbose_name="Número de cuota")
    fecha_vencimiento = models.DateField(verbose_name="Fecha de vencimiento")
    
    # Desglose de la cuota
    capital_a_pagar = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        help_text="Porción de la cuota que amortiza el capital"
    )
    interes_a_pagar = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        help_text="Porción de la cuota que cubre intereses"
    )
    valor_cuota = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        help_text="Valor total de la cuota (capital + intereses)"
    )
    saldo_capital_pendiente = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        help_text="Saldo de capital pendiente después de pagar esta cuota"
    )
    
    # Estado de la cuota
    pagada = models.BooleanField(default=False, verbose_name="¿Cuota pagada?")
    fecha_pago = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha en que se pagó la cuota"
    )
    monto_pagado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Monto efectivamente pagado (puede diferir del valor_cuota)"
    )
    fecha_ultimo_recordatorio_pagador = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Ultima vez que se incluyo esta cuota en el resumen mensual al pagador.',
    )
    fecha_ultimo_aviso_usuario_mora = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Ultima vez que se notifico al usuario por atraso posterior al resumen al pagador.',
    )

    class Meta:
        ordering = ['credito', 'numero_cuota']
        unique_together = ('credito', 'numero_cuota')
        verbose_name = 'Cuota de Amortización'
        verbose_name_plural = 'Cuotas de Amortización'

    def __str__(self):
        estado = "Pagada" if self.pagada else "Pendiente"
        return f"Cuota {self.numero_cuota}/{self.credito.plazo} - {self.credito.numero_credito} ({estado})"


class DetalleContablePago(models.Model):
    class MetodologiaCalculo(models.TextChoices):
        CUOTA_INTERES_PRIMERO = 'CUOTA_INTERES_PRIMERO', 'Interes primero sobre cuota'
        ABONO_CAPITAL_DIRECTO = 'ABONO_CAPITAL_DIRECTO', 'Abono directo a capital'

    pago = models.ForeignKey(
        HistorialPago,
        on_delete=models.CASCADE,
        related_name='detalles_contables',
    )
    credito = models.ForeignKey(
        Credito,
        on_delete=models.CASCADE,
        related_name='detalles_contables_pago',
    )
    cuota = models.ForeignKey(
        CuotaAmortizacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='detalles_contables_pago',
    )
    secuencia_aplicacion = models.PositiveIntegerField(default=1)
    fecha_aplicacion = models.DateTimeField(default=timezone.now)
    monto_total_aplicado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    capital_aplicado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Porcion del pago aplicada al capital financiado de la cuota.',
    )
    interes_aplicado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Porcion del pago aplicada a intereses de la cuota.',
    )
    capital_principal_aplicado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Porcion del capital aplicado atribuida al monto aprobado original.',
    )
    comision_aplicada = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Porcion del capital aplicado atribuida a la comision financiada.',
    )
    iva_aplicado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Porcion del capital aplicado atribuida al IVA financiado.',
    )
    metodologia_calculo = models.CharField(
        max_length=32,
        choices=MetodologiaCalculo.choices,
        default=MetodologiaCalculo.CUOTA_INTERES_PRIMERO,
        help_text='Metodo usado para desglosar contablemente la aplicacion del pago.',
    )

    class Meta:
        ordering = ['pago', 'secuencia_aplicacion', 'id']
        verbose_name = 'Detalle contable de pago'
        verbose_name_plural = 'Detalles contables de pago'
        indexes = [
            models.Index(fields=['pago', 'secuencia_aplicacion'], name='idx_detcont_pago_seq'),
            models.Index(fields=['credito', 'fecha_aplicacion'], name='idx_detcont_cred_fecha'),
        ]

    def __str__(self):
        cuota_label = f'Cuota {self.cuota.numero_cuota}' if self.cuota_id else 'Sin cuota'
        return f'{self.pago.referencia_pago} - {cuota_label} - ${self.monto_total_aplicado}'


#? ----- MODELOS DE BILLETERA DIGITAL -----

class CuentaAhorro(models.Model):
    """
    Cuenta de ahorro para cualquier usuario (con o sin crédito).
    Relación opcional con User para permitir usuarios inversionistas sin créditos.
    """
    class TipoUsuario(models.TextChoices):
        INVERSIONISTA = 'INVERSIONISTA', 'Inversionista'
        EMPRENDEDOR = 'EMPRENDEDOR', 'Cliente Emprendimiento'
        EMPLEADO = 'EMPLEADO', 'Cliente Libranza'
        NATURAL = 'NATURAL', 'Persona Natural'
    
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='cuenta_ahorro'
    )
    tipo_usuario = models.CharField(max_length=20, choices=TipoUsuario.choices)
    
    # Campos financieros
    saldo_disponible = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Saldo actual disponible en la cuenta"
    )
    saldo_objetivo = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=1000000,
        help_text="Meta de ahorro del usuario"
    )
    
    # Métricas de impacto social (calculadas automáticamente)
    emprendimientos_financiados = models.IntegerField(
        default=0,
        help_text="Número de emprendimientos financiados con fondos de esta cuenta"
    )
    familias_beneficiadas = models.IntegerField(
        default=0,
        help_text="Número de familias beneficiadas indirectamente"
    )
    
    # Fechas
    fecha_apertura = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    
    # Estado
    activa = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = 'Cuenta de Ahorro'
        verbose_name_plural = 'Cuentas de Ahorro'
        
    def __str__(self):
        return f"Cuenta {self.usuario.username} - ${self.saldo_disponible}"


class MovimientoAhorro(models.Model):
    """
    Registro de todos los movimientos de una cuenta de ahorro.
    """
    class TipoMovimiento(models.TextChoices):
        DEPOSITO_ONLINE = 'DEPOSITO_ONLINE', 'Deposito Online'
        DEPOSITO_OFFLINE = 'DEPOSITO_OFFLINE', 'Consignacion Offline'
        RETIRO = 'RETIRO', 'Retiro'
        INTERES = 'INTERES', 'Interes Generado'
        AJUSTE_ADMIN = 'AJUSTE_ADMIN', 'Ajuste Administrativo'
    
    class EstadoMovimiento(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente Aprobacion'
        APROBADO = 'APROBADO', 'Aprobado'
        RECHAZADO = 'RECHAZADO', 'Rechazado'
        PROCESADO = 'PROCESADO', 'Procesado'
    
    # Relaciones
    cuenta = models.ForeignKey(
        CuentaAhorro, 
        on_delete=models.CASCADE, 
        related_name='movimientos'
    )
    
    # Datos del movimiento
    tipo = models.CharField(max_length=20, choices=TipoMovimiento.choices)
    monto = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        validators=[MinValueValidator(0.01)]
    )
    estado = models.CharField(
        max_length=20, 
        choices=EstadoMovimiento.choices, 
        default=EstadoMovimiento.PENDIENTE
    )
    
    # Comprobante (para consignaciones offline)
    comprobante = models.FileField(
        upload_to='billetera/comprobantes/%Y/%m/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(
            allowed_extensions=['pdf', 'jpg', 'jpeg', 'png']
        )]
    )
    
    # Referencia de transacción
    referencia = models.CharField(max_length=100, unique=True)
    
    # Observaciones
    descripcion = models.CharField(max_length=255, blank=True)
    nota_admin = models.TextField(blank=True, null=True)
    
    # Auditoría
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_procesamiento = models.DateTimeField(null=True, blank=True)
    procesado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos_procesados'
    )
    
    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Movimiento de Ahorro'
        verbose_name_plural = 'Movimientos de Ahorro'
        
    def __str__(self):
        return f"{self.tipo} - ${self.monto} - {self.estado}"


class InvestorAccount(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='investor_account'
    )
    activa = models.BooleanField(default=True)
    moneda = models.CharField(max_length=10, default='COP')
    fecha_apertura = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cuenta inversionista'
        verbose_name_plural = 'Cuentas inversionista'

    def __str__(self):
        return f"Inversionista {self.usuario.email or self.usuario.username}"


class InvestmentPosition(models.Model):
    class EstadoPosicion(models.TextChoices):
        BORRADOR = 'borrador', 'Borrador'
        ACTIVA = 'activa', 'Activa'
        CERRADA = 'cerrada', 'Cerrada'
        CANCELADA = 'cancelada', 'Cancelada'

    account = models.ForeignKey(
        InvestorAccount,
        on_delete=models.CASCADE,
        related_name='positions'
    )
    referencia = models.CharField(max_length=50, unique=True, blank=True)
    titulo = models.CharField(max_length=160)
    estado = models.CharField(max_length=20, choices=EstadoPosicion.choices, default=EstadoPosicion.BORRADOR)
    aporte_inicial = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    capital_activo = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    capital_recuperado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tasa_proyectada_anual = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    fecha_inicio = models.DateField()
    fecha_cierre = models.DateField(null=True, blank=True)
    descripcion = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_inicio', '-created_at']
        verbose_name = 'Posicion de inversion'
        verbose_name_plural = 'Posiciones de inversion'

    def save(self, *args, **kwargs):
        if not self.referencia:
            ultimo = InvestmentPosition.objects.order_by('-id').first()
            numero = (ultimo.id + 1) if ultimo else 1
            self.referencia = f"INV-{numero:06d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.referencia


class InvestmentCashflow(models.Model):
    class TipoCashflow(models.TextChoices):
        APORTE = 'aporte', 'Aporte'
        RETORNO = 'retorno', 'Retorno'
        COMISION = 'comision', 'Comision'
        AJUSTE = 'ajuste', 'Ajuste'
        SALIDA_CAPITAL = 'salida_capital', 'Salida de capital'

    position = models.ForeignKey(
        InvestmentPosition,
        on_delete=models.CASCADE,
        related_name='cashflows'
    )
    tipo = models.CharField(max_length=20, choices=TipoCashflow.choices)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    fecha_efectiva = models.DateField()
    descripcion = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_efectiva', '-created_at']
        verbose_name = 'Movimiento de inversion'
        verbose_name_plural = 'Movimientos de inversion'

    def __str__(self):
        return f"{self.position.referencia} - {self.tipo} - {self.monto}"


class InvestmentReturnSnapshot(models.Model):
    account = models.ForeignKey(
        InvestorAccount,
        on_delete=models.CASCADE,
        related_name='snapshots'
    )
    fecha_corte = models.DateField()
    roi_acumulado = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    roi_mensual = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    tasa_retorno_proyectada = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    capital_activo = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    capital_recuperado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tiempo_promedio_retorno_dias = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_corte', '-created_at']
        verbose_name = 'Snapshot de retorno'
        verbose_name_plural = 'Snapshots de retorno'
        unique_together = ('account', 'fecha_corte')

    def __str__(self):
        return f"{self.account.usuario.email} - {self.fecha_corte}"


class InvestmentEvent(models.Model):
    account = models.ForeignKey(
        InvestorAccount,
        on_delete=models.CASCADE,
        related_name='events'
    )
    position = models.ForeignKey(
        InvestmentPosition,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='events'
    )
    titulo = models.CharField(max_length=120)
    descripcion = models.TextField(blank=True)
    fecha_evento = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_evento']
        verbose_name = 'Evento de inversion'
        verbose_name_plural = 'Eventos de inversion'

    def __str__(self):
        return self.titulo


class ConfiguracionTasaInteres(models.Model):
    """
    Configuración de tasas de interés para cuentas de ahorro.
    Permite ajustar tasas sin modificar código.
    """
    tasa_anual_efectiva = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=5.00,
        help_text="Tasa anual efectiva (EA) en porcentaje"
    )
    fecha_vigencia = models.DateField()
    activa = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = 'Configuración de Tasa'
        verbose_name_plural = 'Configuraciones de Tasas'
        ordering = ['-fecha_vigencia']

    def __str__(self):
        return f"Tasa {self.tasa_anual_efectiva}% EA - {self.fecha_vigencia}"


#? ----- Sistema de Notificaciones -----
class Notificacion(models.Model):
    """
    Modelo para gestionar notificaciones de usuarios.
    Muestra alertas en tiempo real sobre eventos importantes.
    """
    class TipoNotificacion(models.TextChoices):
        CREDITO_APROBADO = 'CREDITO_APROBADO', 'Credito Aprobado'
        CREDITO_RECHAZADO = 'CREDITO_RECHAZADO', 'Credito Rechazado'
        PAGO_RECIBIDO = 'PAGO_RECIBIDO', 'Pago Recibido'
        PAGO_PENDIENTE = 'PAGO_PENDIENTE', 'Pago Pendiente'
        CONSIGNACION_APROBADA = 'CONSIGNACION_APROBADA', 'Consignacion Aprobada'
        CONSIGNACION_RECHAZADA = 'CONSIGNACION_RECHAZADA', 'Consignacion Rechazada'
        DOCUMENTO_PENDIENTE = 'DOCUMENTO_PENDIENTE', 'Documento Pendiente'
        MORA = 'MORA', 'Credito en Mora'
        SISTEMA = 'SISTEMA', 'Notificacion del Sistema'

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notificaciones'
    )
    tipo = models.CharField(
        max_length=30,
        choices=TipoNotificacion.choices
    )
    titulo = models.CharField(max_length=100)
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    url = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text="URL a la que redirige la notificación"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_leida = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Notificación'
        verbose_name_plural = 'Notificaciones'
        ordering = ['-fecha_creacion']
        indexes = [
            models.Index(fields=['usuario', '-fecha_creacion']),
            models.Index(fields=['usuario', 'leida']),
        ]

    def __str__(self):
        return f"{self.titulo} - {self.usuario.email}"

    def marcar_como_leida(self):
        """Marca la notificación como leída"""
        if not self.leida:
            from django.utils import timezone
            self.leida = True
            self.fecha_leida = timezone.now()
            self.save(update_fields=['leida', 'fecha_leida'])


#? ----- Modelo de reestructuración de crédito -----
class ReestructuracionCredito(models.Model):
    """
    Registra las reestructuraciones realizadas a un crédito cuando se hacen abonos
    mayores a 2 cuotas o abonos a capital.
    """
    class TipoAbono(models.TextChoices):
        NORMAL = 'NORMAL', 'Abono Normal'
        CAPITAL = 'CAPITAL', 'Abono a Capital - reducir cuota'
        CAPITAL_REDUCIR_PLAZO = (
            'CAPITAL_REDUCIR_PLAZO',
            'Abono a Capital - reducir plazo',
        )
        MAYOR = 'MAYOR', 'Abono Mayor (>2 cuotas)'

    credito = models.ForeignKey(
        Credito,
        on_delete=models.CASCADE,
        related_name='reestructuraciones'
    )
    fecha_reestructuracion = models.DateTimeField(auto_now_add=True)

    # Información del abono
    monto_abonado = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Monto total abonado que generó la reestructuración"
    )
    tipo_abono = models.CharField(
        max_length=32,
        choices=TipoAbono.choices,
        default=TipoAbono.NORMAL
    )

    # Plan de pagos antes y después
    plan_anterior = models.JSONField(
        help_text="Plan de pagos antes del abono (JSON con cuotas restantes)"
    )
    plan_nuevo = models.JSONField(
        help_text="Plan de pagos después del abono (JSON con cuotas recalculadas)"
    )

    # Datos financieros antes de la reestructuración
    saldo_pendiente_anterior = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Saldo pendiente antes del abono"
    )
    capital_pendiente_anterior = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Capital pendiente antes del abono"
    )
    plazo_restante_anterior = models.IntegerField(
        help_text="Cuotas restantes antes del abono"
    )

    # Datos financieros después de la reestructuración
    saldo_pendiente_nuevo = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Saldo pendiente después del abono"
    )
    capital_pendiente_nuevo = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Capital pendiente después del abono"
    )
    plazo_restante_nuevo = models.IntegerField(
        help_text="Cuotas restantes después del abono"
    )

    # Beneficios de la reestructuración
    ahorro_intereses = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Ahorro en intereses debido a la reestructuración"
    )
    cuota_mensual_nueva = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Nueva cuota mensual (si cambió)"
    )

    # Aprobación y seguimiento
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reestructuraciones_aprobadas',
        help_text="Usuario que aprobó la reestructuración (puede ser el cliente)"
    )
    observaciones = models.TextField(
        blank=True,
        help_text="Observaciones adicionales sobre la reestructuración"
    )

    # Referencia al pago que generó la reestructuración
    pago_relacionado = models.ForeignKey(
        'HistorialPago',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reestructuracion',
        help_text="Pago que generó esta reestructuración"
    )

    class Meta:
        verbose_name = 'Reestructuración de Crédito'
        verbose_name_plural = 'Reestructuraciones de Crédito'
        ordering = ['-fecha_reestructuracion']
        indexes = [
            models.Index(fields=['credito', '-fecha_reestructuracion']),
            models.Index(fields=['tipo_abono']),
        ]

    def __str__(self):
        return f"Reestructuración {self.credito.numero_credito} - {self.get_tipo_abono_display()} - ${self.monto_abonado}"


#? ----- INTEGRACIÓN ZAPSIGN: Firma Electrónica de Pagarés -----

class Pagare(models.Model):
    """
    Modelo para gestionar pagarés electrónicos firmados vía ZapSign.
    Almacena toda la información de trazabilidad legal y evidencia forense.
    """
    class EstadoPagare(models.TextChoices):
        CREATED = 'CREATED', 'Creado'
        SENT = 'SENT', 'Enviado a ZapSign'
        SIGNED = 'SIGNED', 'Firmado'
        REFUSED = 'REFUSED', 'Rechazado por Cliente'
        CANCELLED = 'CANCELLED', 'Cancelado'

    # Relación con crédito
    credito = models.OneToOneField(
        Credito,
        on_delete=models.CASCADE,
        related_name='pagare'
    )

    # Identificación
    numero_pagare = models.CharField(
        max_length=30,
        unique=True,
        editable=False,
        help_text="Ej: PAG-2026-00123"
    )
    estado = models.CharField(
        max_length=20,
        choices=EstadoPagare.choices,
        default=EstadoPagare.CREATED
    )
    version_plantilla = models.CharField(
        max_length=10,
        default='1.0',
        help_text="Versión de la plantilla legal usada"
    )

    # Archivos PDF
    archivo_pdf = models.FileField(
        upload_to='pagares/%Y/%m/',
        help_text="PDF original generado"
    )
    archivo_pdf_firmado = models.FileField(
        upload_to='pagares_firmados/%Y/%m/',
        null=True,
        blank=True,
        help_text="PDF firmado descargado de ZapSign"
    )
    hash_pdf = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="SHA-256 del PDF original (trazabilidad)"
    )

    # 🔑 Integración ZapSign (campos críticos)
    zapsign_doc_token = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Token del documento en ZapSign (PRIMARY KEY de integración)"
    )
    zapsign_sign_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        help_text="URL de firma enviada al cliente"
    )
    zapsign_signed_file_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        help_text="URL del PDF firmado en ZapSign"
    )
    zapsign_status = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="Status reportado por ZapSign (pending, signed, refused)"
    )

    # 📅 Fechas (auditoría temporal)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_envio = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Cuándo se envió a ZapSign"
    )
    fecha_firma = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp de firma del cliente (from webhook)"
    )
    fecha_rechazo = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Si el cliente rechazó firmar"
    )

    # Evidencia Forense (trazabilidad legal)
    ip_firmante = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP del cliente al firmar (evidencia)"
    )
    evidencias = models.JSONField(
        default=dict,
        blank=True,
        help_text="Datos completos del webhook (auditoría)"
    )

    # 👤 Auditoría de creación
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='pagares_creados',
        help_text="Usuario que generó el pagaré"
    )

    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Pagaré'
        verbose_name_plural = 'Pagarés'
        indexes = [
            models.Index(fields=['zapsign_doc_token']),
            models.Index(fields=['estado', 'fecha_creacion']),
        ]

    def __str__(self):
        return f"{self.numero_pagare} - {self.credito.numero_credito} ({self.get_estado_display()})"

    def save(self, *args, **kwargs):
        if not self.numero_pagare:
            from django.utils import timezone
            ultimo = Pagare.objects.order_by('-id').first()
            numero = (ultimo.id + 1) if ultimo else 1
            self.numero_pagare = f"PAG-{timezone.now().year}-{numero:05d}"
        super().save(*args, **kwargs)


class ZapSignWebhookLog(models.Model):
    """
    Registro de todos los webhooks recibidos de ZapSign.
    Auditoría completa para trazabilidad legal y debugging.
    """
    # Identificación
    doc_token = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Token del documento de ZapSign"
    )
    event = models.CharField(
        max_length=50,
        help_text="Tipo de evento (doc_signed, doc_viewed, etc)"
    )

    # Contenido completo
    payload = models.JSONField(
        help_text="Payload completo del webhook"
    )
    headers = models.JSONField(
        default=dict,
        help_text="Headers HTTP recibidos"
    )

    # Validación
    signature_valid = models.BooleanField(
        default=False,
        help_text="Si la firma/secret fue validada correctamente"
    )
    processed = models.BooleanField(
        default=False,
        help_text="Si el webhook fue procesado exitosamente"
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Mensaje de error si el procesamiento falló"
    )

    # Metadata
    received_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(
        help_text="IP desde donde vino el webhook"
    )

    class Meta:
        ordering = ['-received_at']
        verbose_name = 'Log de Webhook ZapSign'
        verbose_name_plural = 'Logs de Webhooks ZapSign'
        indexes = [
            models.Index(fields=['doc_token', '-received_at']),
            models.Index(fields=['event', 'processed']),
        ]

    def __str__(self):
        status = "OK" if self.processed else "ERROR"
        return f"{status} {self.event} - {self.doc_token} ({self.received_at})"


class WhatsAppInternalApplication(models.Model):
    class ProductType(models.TextChoices):
        PAYROLL_LOAN = 'payroll_loan', 'Libranza'
        WHATSAPP_CREDIT = 'whatsapp_credit', 'Credito WhatsApp'

    class Status(models.TextChoices):
        RECEIVED = 'received', 'Recibida'
        PENDING_PAYROLL_VALIDATION = 'pending_payroll_validation', 'Pendiente validacion convenio'
        PENDING_FORM_COMPLETION = 'pending_form_completion', 'Pendiente completar formulario'
        REJECTED = 'rejected', 'Rechazada'

    product_type = models.CharField(max_length=32, choices=ProductType.choices)
    source = models.CharField(max_length=32, default='whatsapp')
    status = models.CharField(max_length=40, choices=Status.choices, default=Status.RECEIVED)

    tipo_documento = models.CharField(max_length=10)
    numero_documento = models.CharField(max_length=30, db_index=True)
    nombres = models.CharField(max_length=120)
    apellidos = models.CharField(max_length=120)
    celular = models.CharField(max_length=30)
    correo = models.EmailField(blank=True)
    ciudad = models.CharField(max_length=120, blank=True)
    ocupacion = models.CharField(max_length=120, blank=True)
    ingresos_mensuales = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    monto_solicitado = models.DecimalField(max_digits=12, decimal_places=2)
    plazo_meses = models.PositiveSmallIntegerField()

    empresa = models.ForeignKey(Empresa, on_delete=models.SET_NULL, null=True, blank=True)
    convenio_validado = models.BooleanField(default=False)
    vinculo_laboral_validado = models.BooleanField(default=False)

    autorizacion_tratamiento_datos = models.BooleanField(default=False)
    autorizacion_validacion_informacion = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product_type', 'numero_documento', '-created_at'], name='wa_app_product_doc_idx'),
            models.Index(fields=['status', '-created_at'], name='wa_app_status_created_idx'),
        ]
        verbose_name = 'Solicitud interna WhatsApp'
        verbose_name_plural = 'Solicitudes internas WhatsApp'

    def __str__(self):
        return f"{self.product_type} {self.numero_documento} ({self.status})"


class CreditoReglaEspecialAudit(models.Model):
    credito = models.ForeignKey(
        Credito,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reglas_especiales_audit',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reglas_especiales_libranza_creadas',
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    term_months = models.PositiveSmallIntegerField()
    monthly_rate = models.DecimalField(max_digits=7, decimal_places=4)
    commission_rate = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    commission_amount = models.DecimalField(max_digits=14, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2)
    estimated_monthly_payment = models.DecimalField(max_digits=14, decimal_places=2)
    estimated_total_payment = models.DecimalField(max_digits=14, decimal_places=2)
    estimated_interest = models.DecimalField(max_digits=14, decimal_places=2)
    simulation_payload = models.JSONField(default=dict, blank=True)
    business_reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at'], name='special_audit_created_idx'),
            models.Index(fields=['credito', '-created_at'], name='special_audit_credit_idx'),
        ]
        verbose_name = 'Auditoria regla especial libranza'
        verbose_name_plural = 'Auditorias reglas especiales libranza'

    def __str__(self):
        credito_ref = self.credito.numero_credito if self.credito_id else 'sin credito'
        return f"Regla especial {credito_ref} - ${self.amount} / {self.term_months} meses"


class WhatsAppInternalConsent(models.Model):
    class ProductType(models.TextChoices):
        PAYROLL_LOAN = 'payroll_loan', 'Libranza'
        WHATSAPP_CREDIT = 'whatsapp_credit', 'Credito WhatsApp'

    product_type = models.CharField(max_length=32, choices=ProductType.choices)
    source = models.CharField(max_length=32, default='whatsapp')
    document_number = models.CharField(max_length=30, db_index=True)
    phone = models.CharField(max_length=30, blank=True)
    consent_type = models.CharField(max_length=80)
    accepted = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product_type', 'document_number', 'consent_type'], name='wa_consent_product_doc_idx'),
            models.Index(fields=['-created_at'], name='wa_consent_created_idx'),
        ]
        verbose_name = 'Consentimiento interno WhatsApp'
        verbose_name_plural = 'Consentimientos internos WhatsApp'

    def __str__(self):
        return f"{self.product_type} {self.consent_type} ({self.accepted})"


class WhatsAppInternalAPIAuditLog(models.Model):
    action = models.CharField(max_length=80)
    product_type = models.CharField(max_length=32, blank=True)
    request_id = models.CharField(max_length=80, blank=True)
    correlation_id = models.CharField(max_length=80, blank=True)
    document_number_hash = models.CharField(max_length=64, blank=True, db_index=True)
    document_number_masked = models.CharField(max_length=20, blank=True)
    phone_masked = models.CharField(max_length=20, blank=True)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=255)
    status_code = models.PositiveSmallIntegerField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action', '-created_at'], name='wa_audit_action_created_idx'),
            models.Index(fields=['document_number_hash'], name='wa_audit_doc_hash_idx'),
        ]
        verbose_name = 'Auditoria API interna WhatsApp'
        verbose_name_plural = 'Auditoria API interna WhatsApp'

    def __str__(self):
        return f"{self.action} {self.status_code} ({self.created_at})"

