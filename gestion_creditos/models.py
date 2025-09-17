from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator, FileExtensionValidator
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
        FIRMADO = 'FIRMADO', 'Firmado' # Nuevo estado para la firma (conexion con autentic)
        ACTIVO = 'ACTIVO', 'Activo'
        EN_MORA = 'EN_MORA', 'En Mora'
        PAGADO = 'PAGADO', 'Pagado'

    # Campos comunes a todos los créditos
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='creditos')
    numero_credito = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    linea = models.CharField(max_length=20, choices=LineaCredito.choices)
    estado = models.CharField(max_length=20, choices=EstadoCredito.choices, default=EstadoCredito.SOLICITUD)
    documento_enviado = models.BooleanField(default=False, help_text="Indica si el pagaré ha sido enviado para firma.")
    
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_solicitud']
        verbose_name = 'Crédito'
        verbose_name_plural = 'Créditos'

    def __str__(self):
        return f'{self.get_linea_display()} {self.numero_credito} - {self.usuario.username}'

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
    saldo_pendiente = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
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

    #? Información del crédito
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
