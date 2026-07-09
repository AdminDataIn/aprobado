from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from gestion_creditos.models import Empresa


class ContractorApplication(models.Model):
    class Estado(models.TextChoices):
        BORRADOR = 'BORRADOR', 'Borrador'
        DOCUMENTOS_PENDIENTES = 'DOCUMENTOS_PENDIENTES', 'Documentos pendientes'
        DOCUMENTOS_CARGADOS = 'DOCUMENTOS_CARGADOS', 'Documentos cargados'
        EN_REVISION = 'EN_REVISION', 'En revision'

    class EscenarioCredito(models.TextChoices):
        NUEVO_CREDITO = 'NUEVO_CREDITO', 'Nuevo credito'
        SEGUNDO_CREDITO = 'SEGUNDO_CREDITO', 'Segundo credito'
        RECOGIDA_CARTERA = 'RECOGIDA_CARTERA', 'Recogida de cartera'

    class TipoDocumento(models.TextChoices):
        CEDULA_CIUDADANIA = 'CC', 'Cedula de ciudadania'
        CEDULA_EXTRANJERIA = 'CE', 'Cedula de extranjeria'

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='solicitudes_prestadores',
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        related_name='solicitudes_prestadores',
    )
    escenario_credito = models.CharField(
        max_length=32,
        choices=EscenarioCredito.choices,
        default=EscenarioCredito.NUEVO_CREDITO,
    )
    tipo_documento = models.CharField(max_length=4, choices=TipoDocumento.choices)
    numero_documento = models.CharField(max_length=20)
    nombres = models.CharField(max_length=120)
    apellidos = models.CharField(max_length=120)
    celular = models.CharField(max_length=20)
    correo = models.EmailField()
    direccion = models.CharField(max_length=255)
    cargo = models.CharField(max_length=160)
    fecha_inicio_contrato = models.DateField(null=True, blank=True)
    fecha_fin_contrato = models.DateField(null=True, blank=True)
    valor_total_contrato = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    valor_pendiente_cobrar = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    monto_solicitado = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    plazo_meses = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )
    estado = models.CharField(
        max_length=32,
        choices=Estado.choices,
        default=Estado.DOCUMENTOS_PENDIENTES,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Solicitud de prestador'
        verbose_name_plural = 'Solicitudes de prestadores'
        indexes = [
            models.Index(fields=['usuario', '-created_at'], name='prestador_usuario_fecha_idx'),
            models.Index(fields=['empresa', 'estado'], name='prestador_empresa_estado_idx'),
            models.Index(fields=['numero_documento'], name='prestador_documento_idx'),
        ]

    def __str__(self):
        return f'{self.nombres} {self.apellidos} - {self.numero_documento}'

    @property
    def nombre_completo(self):
        return f'{self.nombres} {self.apellidos}'.strip()


def ruta_documento_prestador(instance, filename):
    extension = Path(filename or '').suffix.lower()
    return f'prestadores/solicitudes/{instance.solicitud_id}/{instance.tipo_documento}{extension}'


class ContractorApplicationDocument(models.Model):
    class TipoDocumento(models.TextChoices):
        CEDULA_FRONTAL = 'CEDULA_FRONTAL', 'Cedula frontal'
        CEDULA_TRASERA = 'CEDULA_TRASERA', 'Cedula trasera'
        CONTRATO = 'CONTRATO', 'Contrato vigente'
        CERTIFICADO_BANCARIO = 'CERTIFICADO_BANCARIO', 'Certificado bancario'

    solicitud = models.ForeignKey(
        ContractorApplication,
        on_delete=models.CASCADE,
        related_name='documentos',
    )
    tipo_documento = models.CharField(max_length=32, choices=TipoDocumento.choices)
    archivo = models.FileField(upload_to=ruta_documento_prestador)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='documentos_prestadores_cargados',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tipo_documento']
        verbose_name = 'Documento de prestador'
        verbose_name_plural = 'Documentos de prestadores'
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud', 'tipo_documento'],
                name='unique_documento_por_solicitud_prestador',
            ),
        ]

    def __str__(self):
        return f'{self.solicitud_id} - {self.tipo_documento}'

    def clean(self):
        super().clean()
        nombre = (getattr(self.archivo, 'name', '') or '').lower()
        extension = Path(nombre).suffix
        if self.tipo_documento in {
            self.TipoDocumento.CONTRATO,
            self.TipoDocumento.CERTIFICADO_BANCARIO,
        } and extension != '.pdf':
            raise ValidationError({'archivo': 'Este documento debe cargarse en PDF.'})
        if self.tipo_documento in {
            self.TipoDocumento.CEDULA_FRONTAL,
            self.TipoDocumento.CEDULA_TRASERA,
        } and extension not in {'.jpg', '.jpeg', '.png', '.pdf'}:
            raise ValidationError({'archivo': 'Carga una imagen o PDF valido.'})


DOCUMENTOS_OBLIGATORIOS_PRESTADOR = (
    ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL,
    ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA,
    ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
    ContractorApplicationDocument.TipoDocumento.CONTRATO,
)
