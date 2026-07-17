from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

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

    class EstadoContrato(models.TextChoices):
        VIGENTE = 'VIGENTE', 'Vigente'
        VENCIDO = 'VENCIDO', 'Vencido'
        SUSPENDIDO = 'SUSPENDIDO', 'Suspendido'
        TERMINADO = 'TERMINADO', 'Terminado'
        LIQUIDADO = 'LIQUIDADO', 'Liquidado'
        NO_DETERMINABLE = 'NO_DETERMINABLE', 'No determinable'

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
    duracion_contrato_meses = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text='Duracion contractual explicita en meses calendario.',
    )
    estado_contractual_declarado = models.CharField(
        max_length=20,
        choices=EstadoContrato.choices,
        default=EstadoContrato.NO_DETERMINABLE,
    )
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
    version_configuracion_financiera_simulacion = models.CharField(max_length=80, blank=True)
    version_politica_simulacion = models.CharField(max_length=80, blank=True)
    monto_simulado = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    plazo_simulado_meses = models.PositiveSmallIntegerField(null=True, blank=True)
    tasa_mensual_simulacion = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
    )
    monto_maximo_configuracion_simulacion = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    plazo_maximo_configuracion_simulacion = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    simulada_en = models.DateTimeField(null=True, blank=True)
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
    version_configuracion_financiera = models.CharField(max_length=80, blank=True)
    tasa_mensual_configuracion = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
    )
    monto_maximo_configuracion = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    plazo_maximo_configuracion = models.PositiveSmallIntegerField(null=True, blank=True)
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
        permissions = [
            (
                'can_evaluate_contractor_application',
                'Puede ejecutar la evaluacion formal de prestadores',
            ),
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
        DATACREDITO_REUTILIZADO = 'DATACREDITO_REUTILIZADO', 'DataCrédito reutilizado'
        DATACREDITO_CONSULTADO = 'DATACREDITO_CONSULTADO', 'DataCrédito consultado'
        DATACREDITO_ERROR = 'DATACREDITO_ERROR', 'Error DataCrédito'

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


class AutorizacionConsultaDatacreditoPrestador(models.Model):
    solicitud = models.ForeignKey(
        ContractorApplication,
        on_delete=models.PROTECT,
        related_name='autorizaciones_datacredito',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='autorizaciones_datacredito_prestador',
    )
    autorizada = models.BooleanField()
    version_texto = models.CharField(max_length=80)
    texto_hash = models.CharField(max_length=64)
    aceptada_en = models.DateTimeField()
    ip_hash = models.CharField(max_length=64, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'contractors_autorizaciondatacredito_v2'
        ordering = ['-aceptada_en', '-id']
        verbose_name = 'Autorización DataCrédito de prestador'
        verbose_name_plural = 'Autorizaciones DataCrédito de prestadores'
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'solicitud',
                    'usuario',
                    'autorizada',
                    'version_texto',
                    'texto_hash',
                ],
                name='unique_autorizacion_datacredito_v2',
            ),
        ]
        indexes = [
            models.Index(
                fields=['solicitud', '-aceptada_en'],
                name='prest_auth_dc_solic_idx',
            ),
            models.Index(
                fields=['version_texto', 'texto_hash'],
                name='prest_auth_dc_version_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('La evidencia de autorización es inmutable.')
        return super().save(*args, **kwargs)

    def __str__(self):
        estado = 'autorizada' if self.autorizada else 'no autorizada'
        return f'Solicitud {self.solicitud_id} - {self.version_texto} - {estado}'


class ConfiguracionSimuladorPrestador(models.Model):
    activo = models.BooleanField(default=True)
    nombre = models.CharField(max_length=120, default='Simulador Prestadores')
    version = models.CharField(
        max_length=80,
        blank=True,
        help_text='Version administrativa de la configuracion financiera.',
    )
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
        constraints = [
            models.UniqueConstraint(
                fields=['version'],
                condition=~Q(version=''),
                name='unique_simulador_prestador_version',
            ),
        ]
        verbose_name = 'Configuración del simulador de prestadores'
        verbose_name_plural = 'Configuraciones del simulador de prestadores'

    def __str__(self):
        return self.nombre


class ConfiguracionScorePrestador(models.Model):
    class AccionExcesoCapacidad(models.TextChoices):
        BLOQUEAR = 'BLOQUEAR', 'Bloquear en modo read-only'
        REVISION = 'REVISION', 'Enviar a revision manual'

    nombre = models.CharField(max_length=120)
    version = models.CharField(max_length=80, unique=True)
    activa = models.BooleanField(default=False)
    fecha_vigencia_desde = models.DateField()
    fecha_vigencia_hasta = models.DateField(null=True, blank=True)
    configuracion_financiera = models.ForeignKey(
        ConfiguracionSimuladorPrestador,
        on_delete=models.PROTECT,
        related_name='politicas_score',
        null=True,
        blank=True,
        help_text='Configuracion financiera exacta aplicable a esta politica.',
    )

    peso_datacredito = models.DecimalField(max_digits=6, decimal_places=5)
    peso_capacidad = models.DecimalField(max_digits=6, decimal_places=5)
    peso_comportamiento = models.DecimalField(max_digits=6, decimal_places=5)
    peso_riesgo = models.DecimalField(max_digits=6, decimal_places=5)
    peso_referencias = models.DecimalField(max_digits=6, decimal_places=5)

    score_premium_min = models.PositiveSmallIntegerField(default=850)
    score_alta_min = models.PositiveSmallIntegerField(default=750)
    score_media_min = models.PositiveSmallIntegerField(default=680)
    score_entrada_min = models.PositiveSmallIntegerField(default=600)
    cuota_ingreso_maxima = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('1'))],
    )
    monto_maximo_politica = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    plazo_maximo_politica = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
    )
    tasa_mensual_referencia = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Porcentaje mensual de referencia. Ejemplo: 2.2000.',
    )
    penalizacion_geolocalizacion = models.PositiveSmallIntegerField(default=80)
    umbral_geolocalizacion = models.PositiveSmallIntegerField(default=600)
    mora_bloqueo_dias = models.PositiveSmallIntegerField(default=90)
    consultas_recientes_revision = models.PositiveSmallIntegerField(default=6)
    requiere_referencias = models.BooleanField(default=False)
    permite_redistribuir_pesos_faltantes = models.BooleanField(default=False)
    accion_exceso_capacidad = models.CharField(
        max_length=16,
        choices=AccionExcesoCapacidad.choices,
        default=AccionExcesoCapacidad.REVISION,
    )
    version_score = models.CharField(max_length=80)
    version_politica = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_vigencia_desde', '-id']
        verbose_name = 'Configuracion de score de prestadores'
        verbose_name_plural = 'Configuraciones de score de prestadores'
        constraints = [
            models.UniqueConstraint(
                fields=['activa'],
                condition=Q(activa=True),
                name='unique_score_prestador_activo',
            ),
        ]

    def clean(self):
        super().clean()
        pesos = (
            self.peso_datacredito,
            self.peso_capacidad,
            self.peso_comportamiento,
            self.peso_riesgo,
            self.peso_referencias,
        )
        if any(peso is None or peso < 0 or peso > 1 for peso in pesos):
            raise ValidationError('Cada peso debe estar entre 0 y 1.')
        if abs(sum(pesos, Decimal('0')) - Decimal('1')) > Decimal('0.00001'):
            raise ValidationError('Los pesos del score deben sumar 1.00000.')
        if not (
            1000 >= self.score_premium_min > self.score_alta_min
            > self.score_media_min > self.score_entrada_min >= 0
        ):
            raise ValidationError('Los umbrales deben ser descendentes y estar entre 0 y 1000.')
        if self.umbral_geolocalizacion > 1000:
            raise ValidationError({'umbral_geolocalizacion': 'El umbral no puede superar 1000.'})
        if self.fecha_vigencia_hasta and self.fecha_vigencia_hasta < self.fecha_vigencia_desde:
            raise ValidationError('La fecha final de vigencia no puede ser anterior a la inicial.')
        if self.activa:
            if not self.configuracion_financiera_id:
                raise ValidationError({
                    'configuracion_financiera': (
                        'Una politica activa requiere configuracion financiera vinculada.'
                    ),
                })
            configuracion = self.configuracion_financiera
            if not configuracion.activo or not configuracion.version:
                raise ValidationError({
                    'configuracion_financiera': (
                        'La configuracion financiera debe estar activa y versionada.'
                    ),
                })
            inconsistencias = []
            if self.monto_maximo_politica != configuracion.monto_maximo:
                inconsistencias.append('monto maximo')
            if self.plazo_maximo_politica != configuracion.plazo_maximo_meses:
                inconsistencias.append('plazo maximo')
            if self.tasa_mensual_referencia != configuracion.tasa_mensual:
                inconsistencias.append('tasa mensual')
            if inconsistencias:
                raise ValidationError({
                    'configuracion_financiera': (
                        'La politica no coincide con su configuracion financiera: '
                        + ', '.join(inconsistencias)
                        + '.'
                    ),
                })
        self._validar_inmutabilidad_si_fue_usada()

    def _validar_inmutabilidad_si_fue_usada(self):
        if not self.pk:
            return
        anterior = type(self).objects.filter(pk=self.pk).first()
        if anterior is None or not PredecisionPrestadorAudit.objects.filter(
            version_politica=anterior.version_politica
        ).exists():
            return
        campos_semanticos = (
            'version', 'peso_datacredito', 'peso_capacidad', 'peso_comportamiento',
            'peso_riesgo', 'peso_referencias', 'score_premium_min', 'score_alta_min',
            'score_media_min', 'score_entrada_min', 'cuota_ingreso_maxima',
            'monto_maximo_politica', 'plazo_maximo_politica',
            'tasa_mensual_referencia', 'penalizacion_geolocalizacion',
            'umbral_geolocalizacion', 'mora_bloqueo_dias',
            'consultas_recientes_revision', 'requiere_referencias',
            'permite_redistribuir_pesos_faltantes', 'accion_exceso_capacidad',
            'version_score', 'version_politica', 'configuracion_financiera_id',
        )
        if any(getattr(anterior, campo) != getattr(self, campo) for campo in campos_semanticos):
            raise ValidationError(
                'Una politica usada en auditorias no puede cambiar de significado; crea una nueva version.'
            )

    def __str__(self):
        return f'{self.nombre} ({self.version})'


class BandaScorePrestador(models.Model):
    class Nombre(models.TextChoices):
        PREMIUM = 'PREMIUM', 'Premium'
        ALTA = 'ALTA', 'Alta'
        MEDIA = 'MEDIA', 'Media'
        ENTRADA = 'ENTRADA', 'Entrada'
        REVISION = 'REVISION', 'Revision'

    class Resultado(models.TextChoices):
        PREAPROBADO_READ_ONLY = 'PREAPROBADO_READ_ONLY', 'Preaprobado read-only'
        REQUIERE_REVISION_MANUAL = 'REQUIERE_REVISION_MANUAL', 'Requiere revision manual'

    configuracion = models.ForeignKey(
        ConfiguracionScorePrestador,
        on_delete=models.PROTECT,
        related_name='bandas',
    )
    nombre = models.CharField(max_length=16, choices=Nombre.choices)
    score_min = models.PositiveSmallIntegerField()
    score_max = models.PositiveSmallIntegerField(null=True, blank=True)
    monto_maximo = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    plazo_maximo = models.PositiveSmallIntegerField(default=0)
    resultado = models.CharField(max_length=32, choices=Resultado.choices)
    orden = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ['orden', '-score_min']
        verbose_name = 'Banda de score de prestadores'
        verbose_name_plural = 'Bandas de score de prestadores'
        constraints = [
            models.UniqueConstraint(
                fields=['configuracion', 'nombre'],
                name='unique_banda_score_prestador_nombre',
            ),
            models.UniqueConstraint(
                fields=['configuracion', 'orden'],
                name='unique_banda_score_prestador_orden',
            ),
            models.CheckConstraint(
                condition=Q(score_min__lte=1000),
                name='banda_score_prestador_min_lte_1000',
            ),
            models.CheckConstraint(
                condition=Q(score_max__isnull=True) | Q(score_max__lte=1000),
                name='banda_score_prestador_max_lte_1000',
            ),
        ]

    def clean(self):
        super().clean()
        limite_superior = 1000 if self.score_max is None else self.score_max
        if self.score_min > limite_superior:
            raise ValidationError('El score minimo no puede superar el maximo.')
        if not self.configuracion_id:
            return
        bandas = type(self).objects.filter(configuracion_id=self.configuracion_id)
        if self.pk:
            bandas = bandas.exclude(pk=self.pk)
        if bandas.filter(
            score_min__lte=limite_superior,
        ).filter(Q(score_max__isnull=True) | Q(score_max__gte=self.score_min)).exists():
            raise ValidationError('El rango de score se solapa con otra banda configurada.')
        if self.pk:
            anterior = type(self).objects.select_related('configuracion').filter(pk=self.pk).first()
            if anterior and PredecisionPrestadorAudit.objects.filter(
                version_politica=anterior.configuracion.version_politica
            ).exists():
                campos = (
                    'nombre', 'score_min', 'score_max', 'monto_maximo',
                    'plazo_maximo', 'resultado', 'orden',
                )
                if any(getattr(anterior, campo) != getattr(self, campo) for campo in campos):
                    raise ValidationError(
                        'Una banda usada en auditorias no puede modificarse; crea una nueva politica.'
                    )

    def __str__(self):
        return f'{self.configuracion.version} - {self.nombre}'


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
