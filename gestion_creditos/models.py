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
    numero_credito = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    linea = models.CharField(max_length=30, choices=LineaCredito.choices)
    estado = models.CharField(max_length=30, choices=EstadoCredito.choices, default=EstadoCredito.SOLICITUD)
    documento_enviado = models.BooleanField(default=False, help_text="Indica si el pagaré ha sido enviado para firma.")
    
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_solicitud']
        verbose_name = 'Crédito'
        verbose_name_plural = 'Créditos'

    def __str__(self):
        return f'{self.get_linea_display()} {self.numero_credito} - {self.usuario.username}'

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

    # --- Propiedades delegadas para acceso unificado ---
    @property
    def monto_aprobado(self):
        return self.detalle.monto_aprobado if self.detalle else 0

    @property
    def saldo_pendiente(self):
        return self.detalle.saldo_pendiente if self.detalle else 0

    @property
    def plazo(self):
        return self.detalle.plazo if self.detalle else 0

    @property
    def tasa_interes(self):
        return self.detalle.tasa_interes if self.detalle else 0

    @property
    def valor_cuota(self):
        return self.detalle.valor_cuota if self.detalle else 0

    @property
    def fecha_proximo_pago(self):
        return self.detalle.fecha_proximo_pago if self.detalle else None

    @property
    def comision(self):
        return self.detalle.comision if self.detalle else 0

    @property
    def iva_comision(self):
        return self.detalle.iva_comision if self.detalle else 0

    @property
    def total_a_pagar(self):
        return self.detalle.total_a_pagar if self.detalle else 0

    @property
    def dias_en_mora(self):
        from django.utils import timezone
        if self.estado == self.EstadoCredito.EN_MORA and self.fecha_proximo_pago:
            dias = (timezone.now().date() - self.fecha_proximo_pago).days
            return dias if dias > 0 else 0
        return 0

    def save(self, *args, **kwargs):
        #? Validación para impedir que un crédito activo cambie de estado.
        if self.pk:  # Si el objeto ya existe en la BD
            try:
                credito_anterior = Credito.objects.get(pk=self.pk)
                if credito_anterior.estado == self.EstadoCredito.ACTIVO and self.estado != self.EstadoCredito.ACTIVO:
                    raise ValidationError('Un crédito en estado "Activo" no puede cambiar a otro estado.')
            except Credito.DoesNotExist:
                pass # El objeto es nuevo, no hay estado anterior para comparar
        super(Credito, self).save(*args, **kwargs)

#? ----- Modelo de crédito de emprendimiento -----
class CreditoEmprendimiento(models.Model):
    """
    Modelo para créditos de emprendimiento.
    Contiene tanto los campos de la solicitud inicial como los datos del crédito una vez aprobado.
    """
    #!--- Relación con el Crédito Principal ---
    credito = models.OneToOneField(Credito, on_delete=models.CASCADE, related_name='detalle_emprendimiento')

    #! --- Campos de la Solicitud (movidos desde configuraciones.SolicitudCredito) ---
    valor_credito = models.DecimalField(max_digits=10, decimal_places=2)
    plazo = models.IntegerField()
    nombre = models.CharField(max_length=100)
    numero_cedula = models.CharField(max_length=20)
    fecha_nac = models.DateField()
    celular_wh = models.CharField(max_length=20)
    direccion = models.TextField()
    estado_civil = models.CharField(max_length=20)
    numero_personas_cargo = models.IntegerField()
    nombre_negocio = models.CharField(max_length=100)
    ubicacion_negocio = models.TextField()
    tiempo_operando = models.CharField(max_length=50)
    dias_trabajados_sem = models.IntegerField()
    prod_serv_ofrec = models.TextField()
    ingresos_prom_mes = models.CharField(max_length=50)
    cli_aten_day = models.IntegerField()
    inventario = models.CharField(max_length=2)
    nomb_ref_per1 = models.CharField(max_length=100)
    cel_ref_per1 = models.CharField(max_length=20)
    rel_ref_per1 = models.CharField(max_length=50)
    nomb_ref_cl1 = models.CharField(max_length=100)
    cel_ref_cl1 = models.CharField(max_length=20)
    rel_ref_cl1 = models.CharField(max_length=50)
    ref_conoc_lid_com = models.CharField(max_length=2)
    foto_negocio = models.FileField(
        upload_to='fotos_negocios/',
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        help_text="Solo se permiten archivos PDF"
    )
    desc_fotos_neg = models.TextField()
    tipo_cta_mno = models.CharField(max_length=20)
    ahorro_tand_alc = models.CharField(max_length=2)
    depend_h = models.CharField(max_length=2)
    desc_cred_nec = models.TextField()
    redes_soc = models.CharField(max_length=2)
    fotos_prod = models.CharField(max_length=2)

    # --- Campos de Aprobación (existentes, pero ahora opcionales) ---
    monto_aprobado = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tasa_interes = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="Tasa de interés mensual")
    comision = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    iva_comision = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_a_pagar = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    saldo_pendiente = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    capital_original_pendiente = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valor_cuota = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fecha_proximo_pago = models.DateField(null=True, blank=True)
    
    # --- Evaluación del Administrador (Estaba en configuraciones.SolicitudCredito) ---
    puntaje = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"Detalle Emprendimiento para Crédito {self.credito.id}"

#? ----- Modelo de crédito de libranza -----
class CreditoLibranza(models.Model):
    """Modelo para créditos de libranza, basado en el antiguo credito_libranza.CreditoLibranza."""
    #!--- Relación con el Crédito Principal ---
    credito = models.OneToOneField(Credito, on_delete=models.CASCADE, related_name='detalle_libranza')

    #? Información de la solicitud
    valor_credito = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(100000)])
    plazo = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])

    #? Información personal (se mantiene aquí porque es específica de la solicitud de libranza)
    nombres = models.CharField(max_length=100)
    apellidos = models.CharField(max_length=100)
    cedula = models.CharField(max_length=20, unique=True)
    direccion = models.CharField(max_length=255)
    telefono = models.CharField(max_length=20)
    correo_electronico = models.EmailField()

    #? Información laboral
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)

    #? Archivos adjuntos
    cedula_frontal = models.FileField(upload_to='credito_libranza/cedulas/')
    cedula_trasera = models.FileField(upload_to='credito_libranza/cedulas/')
    certificado_laboral = models.FileField(upload_to='credito_libranza/certificados_laborales/')
    desprendible_nomina = models.FileField(upload_to='credito_libranza/desprendibles_nomina/')
    certificado_bancario = models.FileField(upload_to='credito_libranza/certificados_bancarios/')

    #? --- Campos de Aprobación ---
    monto_aprobado = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tasa_interes = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="Tasa de interés mensual")
    comision = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    iva_comision = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_a_pagar = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    saldo_pendiente = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    capital_original_pendiente = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valor_cuota = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fecha_proximo_pago = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"Detalle Libranza para Crédito {self.credito.id}"

    @property
    def nombre_completo(self):
        return f'{self.nombres} {self.apellidos}'

#? ----- Modelo de historial de pagos -----
class HistorialPago(models.Model):
    """Historial de pagos, ahora vinculado al modelo principal de Crédito."""
    class EstadoPago(models.TextChoices):
        EXITOSO = 'EXITOSO', 'Exitoso'
        FALLIDO = 'FALLIDO', 'Fallido'
        PENDIENTE = 'PENDIENTE', 'Pendiente'

    #! Relación con el crédito principal
    credito = models.ForeignKey(Credito, on_delete=models.CASCADE, related_name='historial_pagos')
    fecha_pago = models.DateTimeField(auto_now_add=True)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    referencia_pago = models.CharField(max_length=100, unique=True)
    estado = models.CharField(max_length=20, choices=EstadoPago.choices, default=EstadoPago.PENDIENTE)

    def __str__(self):
        return f"Pago {self.id} para Crédito {self.credito.id} - ${self.monto}"

#? ----- Modelo de historial de estados -----
class HistorialEstado(models.Model):
    """Guarda un registro de cada cambio de estado de un crédito."""
    credito = models.ForeignKey(Credito, on_delete=models.CASCADE, related_name='historial_estados')
    estado_anterior = models.CharField(max_length=30, choices=Credito.EstadoCredito.choices, null=True, blank=True)
    estado_nuevo = models.CharField(max_length=30, choices=Credito.EstadoCredito.choices)
    fecha = models.DateTimeField(auto_now_add=True)
    usuario_modificacion = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    motivo = models.TextField(blank=True, null=True, help_text="Razón o motivo del cambio de estado.")
    comprobante_pago = models.FileField(upload_to='comprobantes_pago/', blank=True, null=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'Historial de Estado'
        verbose_name_plural = 'Historial de Estados'

    def __str__(self):
        return f"Crédito {self.credito.id}: {self.estado_anterior} -> {self.estado_nuevo}"


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
        validators=[MinValueValidator(0)]
    )
    saldo_objetivo = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=1000000,
        help_text="Meta de ahorro del usuario"
    )
    
    # Métricas de impacto social (calculadas automáticamente de los creditos aprobados en emprendimientos)
    emprendimientos_financiados = models.IntegerField(default=0)
    familias_beneficiadas = models.IntegerField(default=0)
    
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