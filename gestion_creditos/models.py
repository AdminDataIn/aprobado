from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator, FileExtensionValidator
from django.core.exceptions import ValidationError
import uuid

#? Modelo movido de credito_libranza (la idea es crear las empresas directamente desde el admin)
class Empresa(models.Model):
    nombre = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

#? ----- Modelo principal de crédito ----
class Credito(models.Model):
    class LineaCredito(models.TextChoices):
        EMPRENDIMIENTO = 'EMPRENDIMIENTO', 'Emprendimiento'
        LIBRANZA = 'LIBRANZA', 'Libranza'

    class EstadoCredito(models.TextChoices):
        SOLICITUD = 'SOLICITUD', 'Solicitud'
        EN_REVISION = 'EN_REVISION', 'En Revisión'
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
        max_digits=10, 
        decimal_places=2, 
        help_text="Monto solicitado por el cliente"
    )
    plazo_solicitado = models.IntegerField(
        help_text="Plazo solicitado en meses"
    )
    monto_aprobado = models.DecimalField(
        max_digits=10, 
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
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Comisión de estudio del crédito"
    )
    iva_comision = models.DecimalField(
        max_digits=10, 
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
        max_digits=10, 
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
                return self.detalle.nombre
            elif self.linea == self.LineaCredito.LIBRANZA and hasattr(self.detalle, 'nombre_completo'):
                return self.detalle.nombre_completo
        # Fallback por si el detalle no está o por alguna razón no tiene nombre
        return self.usuario.get_full_name() or self.usuario.username

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
        return None

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
    cedula = models.CharField(max_length=20, unique=True, verbose_name="Número de cédula")
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

    class Meta:
        verbose_name = 'Detalle de Libranza'
        verbose_name_plural = 'Detalles de Libranza'

    def __str__(self):
        return f"Detalle Libranza - {self.nombre_completo} ({self.credito.numero_credito})"

    @property
    def nombre_completo(self):
        return f'{self.nombres} {self.apellidos}'


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

    #! Relación con el crédito principal
    credito = models.ForeignKey(Credito, on_delete=models.CASCADE, related_name='historial_pagos')
    
    # Información del pago
    fecha_pago = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de pago")
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
        ordering = ['-fecha_pago']
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
            models.Index(fields=['credito', 'status'], name='wompi_intent_credito_status_idx'),
            models.Index(fields=['referencia'], name='wompi_intent_referencia_idx'),
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

    class Meta:
        ordering = ['credito', 'numero_cuota']
        unique_together = ('credito', 'numero_cuota')
        verbose_name = 'Cuota de Amortización'
        verbose_name_plural = 'Cuotas de Amortización'

    def __str__(self):
        estado = "Pagada" if self.pagada else "Pendiente"
        return f"Cuota {self.numero_cuota}/{self.credito.plazo} - {self.credito.numero_credito} ({estado})"


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
        DEPOSITO_ONLINE = 'DEPOSITO_ONLINE', 'Depósito Online'
        DEPOSITO_OFFLINE = 'DEPOSITO_OFFLINE', 'Consignación Offline'
        RETIRO = 'RETIRO', 'Retiro'
        INTERES = 'INTERES', 'Interés Generado'
        AJUSTE_ADMIN = 'AJUSTE_ADMIN', 'Ajuste Administrativo'
    
    class EstadoMovimiento(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente Aprobación'
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
        CREDITO_APROBADO = 'CREDITO_APROBADO', 'Crédito Aprobado'
        CREDITO_RECHAZADO = 'CREDITO_RECHAZADO', 'Crédito Rechazado'
        PAGO_RECIBIDO = 'PAGO_RECIBIDO', 'Pago Recibido'
        PAGO_PENDIENTE = 'PAGO_PENDIENTE', 'Pago Pendiente'
        CONSIGNACION_APROBADA = 'CONSIGNACION_APROBADA', 'Consignación Aprobada'
        CONSIGNACION_RECHAZADA = 'CONSIGNACION_RECHAZADA', 'Consignación Rechazada'
        DOCUMENTO_PENDIENTE = 'DOCUMENTO_PENDIENTE', 'Documento Pendiente'
        MORA = 'MORA', 'Crédito en Mora'
        SISTEMA = 'SISTEMA', 'Notificación del Sistema'

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
        CAPITAL = 'CAPITAL', 'Abono a Capital'
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
        max_length=20,
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

    # 🔐 Evidencia Forense (trazabilidad legal)
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
        status = "✅" if self.processed else "❌"
        return f"{status} {self.event} - {self.doc_token} ({self.received_at})"
