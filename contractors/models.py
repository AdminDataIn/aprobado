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
        EVALUACION_PENDIENTE = 'EVALUACION_PENDIENTE', 'Evaluación pendiente'
        EN_EVALUACION = 'EN_EVALUACION', 'En evaluación'
        EVALUACION_COMPLETADA = 'EVALUACION_COMPLETADA', 'Evaluación completada'
        # Conserva el valor persistido previamente y precisa su semántica operativa.
        EN_REVISION_MANUAL = 'EN_REVISION', 'En revisión manual'

    class EscenarioCredito(models.TextChoices):
        NUEVO_CREDITO = 'NUEVO_CREDITO', 'Nuevo credito'
        SEGUNDO_CREDITO = 'SEGUNDO_CREDITO', 'Segundo credito'
        RECOGIDA_CARTERA = 'RECOGIDA_CARTERA', 'Recogida de cartera'

    class TipoDocumento(models.TextChoices):
        CEDULA_CIUDADANIA = 'CC', 'Cedula de ciudadania'
        CEDULA_EXTRANJERIA = 'CE', 'Cedula de extranjeria'

    class TipoContrato(models.TextChoices):
        PRESTACION_SERVICIOS = 'PRESTACION_SERVICIOS', 'Prestación de servicios'
        LABORAL = 'LABORAL', 'Laboral'
        OTRO = 'OTRO', 'Otro'

    class EstadoAnalisisContractual(models.TextChoices):
        NO_SOLICITADO = 'NO_SOLICITADO', 'No solicitado'
        NO_DISPONIBLE = 'NO_DISPONIBLE', 'No disponible'
        COMPLETADO = 'COMPLETADO', 'Completado'
        CON_ADVERTENCIAS = 'CON_ADVERTENCIAS', 'Con advertencias'
        BLOQUEADO = 'BLOQUEADO', 'Bloqueado'

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
    tipo_contrato = models.CharField(
        max_length=30,
        choices=TipoContrato.choices,
        default=TipoContrato.PRESTACION_SERVICIOS,
    )
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
    valor_pagado_contrato = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    observaciones_contrato = models.TextField(blank=True)
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
    acepta_terminos = models.BooleanField(default=False)
    acepta_politica_privacidad = models.BooleanField(default=False)
    autoriza_analisis_contractual_asistido = models.BooleanField(default=False)
    autoriza_consulta_centrales = models.BooleanField(default=False)
    estado_analisis_contractual = models.CharField(
        max_length=24,
        choices=EstadoAnalisisContractual.choices,
        default=EstadoAnalisisContractual.NO_SOLICITADO,
    )
    metadata_analisis_contractual = models.JSONField(default=dict, blank=True)
    fecha_analisis_contractual = models.DateTimeField(null=True, blank=True)
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


class PredecisionPrestadorAudit(models.Model):
    class EstadoEjecucion(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        EN_PROCESO = 'EN_PROCESO', 'En proceso'
        COMPLETADA = 'COMPLETADA', 'Completada'
        ERROR_CONTROLADO = 'ERROR_CONTROLADO', 'Error controlado'

    class Resultado(models.TextChoices):
        NO_EVALUABLE = 'NO_EVALUABLE', 'No evaluable'
        REQUIERE_REVISION_MANUAL = 'REQUIERE_REVISION_MANUAL', 'Requiere revisión manual'
        BLOQUEADO_READ_ONLY = 'BLOQUEADO_READ_ONLY', 'Bloqueado read-only'
        PREAPROBADO_READ_ONLY = 'PREAPROBADO_READ_ONLY', 'Preaprobado read-only'
        ERROR_CONTROLADO = 'ERROR_CONTROLADO', 'Error controlado'

    solicitud = models.ForeignKey(
        ContractorApplication,
        on_delete=models.PROTECT,
        related_name='auditorias_predecision',
    )
    version_datos = models.CharField(max_length=64)
    clave_idempotencia = models.CharField(max_length=64, unique=True)
    estado_ejecucion = models.CharField(
        max_length=24,
        choices=EstadoEjecucion.choices,
        default=EstadoEjecucion.PENDIENTE,
    )
    resultado = models.CharField(
        max_length=32,
        choices=Resultado.choices,
        default=Resultado.NO_EVALUABLE,
    )
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    version_score = models.CharField(max_length=80, blank=True)
    version_politica = models.CharField(max_length=80)
    razones = models.JSONField(default=list, blank=True)
    alertas = models.JSONField(default=list, blank=True)
    bloqueos = models.JSONField(default=list, blank=True)
    snapshot_entrada = models.JSONField(default=dict, blank=True)
    snapshot_salida = models.JSONField(default=dict, blank=True)
    error_codigo = models.CharField(max_length=80, blank=True)
    error_etapa = models.CharField(max_length=80, blank=True)
    iniciada_en = models.DateTimeField()
    finalizada_en = models.DateTimeField(null=True, blank=True)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias_predecision_prestador_creadas',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'contractors_predecisionprestadoraudit_v2'
        ordering = ['-created_at', '-id']
        verbose_name = 'Auditoría de predecisión de prestador'
        verbose_name_plural = 'Auditorías de predecisión de prestadores'
        indexes = [
            models.Index(fields=['solicitud', '-created_at'], name='prest_audit_solic_fecha_idx'),
            models.Index(fields=['resultado'], name='prest_audit_resultado_idx'),
            models.Index(fields=['estado_ejecucion'], name='prest_audit_ejecucion_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            estado_anterior = type(self).objects.filter(pk=self.pk).values_list(
                'estado_ejecucion', flat=True
            ).first()
            if estado_anterior == self.EstadoEjecucion.COMPLETADA:
                raise ValidationError('Una auditoría completada es inmutable.')
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'Evaluación {self.id} - solicitud {self.solicitud_id}'


class TimelinePrestador(models.Model):
    class TipoEvento(models.TextChoices):
        SOLICITUD_REGISTRADA = 'SOLICITUD_REGISTRADA', 'Solicitud registrada'
        EVALUACION_PENDIENTE = 'EVALUACION_PENDIENTE', 'Evaluación pendiente'
        EVALUACION_INICIADA = 'EVALUACION_INICIADA', 'Evaluación iniciada'
        EVALUACION_COMPLETADA = 'EVALUACION_COMPLETADA', 'Evaluación completada'
        REVISION_MANUAL_REQUERIDA = 'REVISION_MANUAL_REQUERIDA', 'Revisión manual requerida'
        DATOS_MODIFICADOS = 'DATOS_MODIFICADOS', 'Datos modificados'

    solicitud = models.ForeignKey(
        ContractorApplication,
        on_delete=models.PROTECT,
        related_name='timeline_operativo',
    )
    tipo_evento = models.CharField(max_length=40, choices=TipoEvento.choices)
    titulo = models.CharField(max_length=160)
    descripcion = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    visible_cliente = models.BooleanField(default=False)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timeline_prestador_creado',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'contractors_timelineprestador_v2'
        ordering = ['-created_at', '-id']
        verbose_name = 'Evento de timeline de prestador'
        verbose_name_plural = 'Timeline de prestadores'
        indexes = [
            models.Index(fields=['solicitud', '-created_at'], name='prest_timeline_solic_idx'),
            models.Index(fields=['tipo_evento', '-created_at'], name='prest_timeline_tipo_idx'),
        ]

    def __str__(self):
        return f'{self.tipo_evento} - solicitud {self.solicitud_id}'


class ConfiguracionSimuladorPrestador(models.Model):
    activo = models.BooleanField(default=True)
    nombre = models.CharField(max_length=120, default='Simulador Prestadores')
    monto_minimo = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('1000000'))
    monto_maximo = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('10000000'))
    plazo_minimo_meses = models.PositiveSmallIntegerField(default=3)
    plazo_maximo_meses = models.PositiveSmallIntegerField(default=24)
    tasa_mensual = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal('1.9000'),
        help_text='Tasa efectiva mensual expresada como porcentaje. Ejemplo: 1.9.',
    )
    porcentaje_originacion = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal('10.0000'),
        help_text='Porcentaje aplicado sobre el monto solicitado. Ejemplo: 10.',
    )
    porcentaje_iva_originacion = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal('19.0000'),
        help_text='IVA sobre el costo de originación. Ejemplo: 19.',
    )
    porcentaje_fondo_garantia = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal('2.0000'),
        help_text='Fondo de garantía, IVA incluido, sobre el monto solicitado.',
    )
    porcentaje_seguro_vida_primera_cuota = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal('0.3711'),
        help_text='Seguro de vida de la primera cuota sobre el monto solicitado.',
    )
    texto_nota_simulacion = models.TextField(
        blank=True,
        default='',
        help_text='Nota informativa opcional mostrada debajo de la simulación.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        verbose_name = 'Configuración del simulador de prestadores'
        verbose_name_plural = 'Configuraciones del simulador de prestadores'

    def __str__(self):
        return self.nombre


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
    metadata_captura = models.JSONField(default=dict, blank=True)
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
        } and extension not in {'.jpg', '.jpeg', '.png'}:
            raise ValidationError({'archivo': 'Captura una imagen valida de la cedula.'})
        if self.archivo and self.archivo.size > 8 * 1024 * 1024:
            raise ValidationError({'archivo': 'El documento no debe superar 8MB.'})


DOCUMENTOS_OBLIGATORIOS_PRESTADOR = (
    ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL,
    ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA,
    ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
    ContractorApplicationDocument.TipoDocumento.CONTRATO,
)

MAPA_CAMPOS_DOCUMENTOS_PRESTADOR = {
    'documento_identidad_frontal': ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL,
    'documento_identidad_reverso': ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA,
    'certificado_bancario': ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
    'contrato_actual': ContractorApplicationDocument.TipoDocumento.CONTRATO,
}
