import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ConsultaDatacreditoSnapshot(models.Model):
    class Servicio(models.TextChoices):
        DECISOR = 'decisor', 'MiDecisor'
        HISTORIAL = 'historial', 'HDCPlus'

    class Estado(models.TextChoices):
        EN_PROCESO = 'EN_PROCESO', 'En proceso'
        EXITOSO = 'EXITOSO', 'Exitoso'
        SIN_INFORMACION = 'SIN_INFORMACION', 'Sin información'
        ERROR_TRANSITORIO = 'ERROR_TRANSITORIO', 'Error transitorio'
        ERROR_PERMANENTE = 'ERROR_PERMANENTE', 'Error permanente'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ambiente = models.CharField(max_length=16, db_index=True)
    servicio = models.CharField(max_length=20, choices=Servicio.choices, db_index=True)
    documento_hash = models.CharField(max_length=64, db_index=True)
    documento_enmascarado = models.CharField(max_length=32)
    fingerprint = models.CharField(max_length=64, db_index=True)
    estado = models.CharField(max_length=24, choices=Estado.choices, db_index=True)
    resultado_normalizado = models.JSONField(default=dict, blank=True)
    codigo_http = models.PositiveSmallIntegerField(null=True, blank=True)
    codigo_funcional = models.CharField(max_length=60, blank=True)
    consultado_en = models.DateTimeField()
    vigente_hasta = models.DateTimeField()
    autorizacion_referencia = models.CharField(max_length=64)
    error_codigo = models.CharField(max_length=80, blank=True)
    error_tipo = models.CharField(max_length=80, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='snapshots_datacredito_prestadores_creados',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'integrations_consultadatacreditosnapshot_v2'
        ordering = ['-consultado_en', '-created_at']
        verbose_name = 'Snapshot DataCrédito'
        verbose_name_plural = 'Snapshots DataCrédito'
        constraints = [
            models.UniqueConstraint(
                fields=['fingerprint'],
                condition=Q(estado='EN_PROCESO'),
                name='unique_datacredito_v2_en_proceso',
            ),
        ]
        indexes = [
            models.Index(
                fields=['ambiente', 'servicio', 'fingerprint', '-vigente_hasta'],
                name='datacred_v2_reuse_idx',
            ),
            models.Index(
                fields=['documento_hash', '-consultado_en'],
                name='datacred_v2_documento_idx',
            ),
            models.Index(fields=['estado', '-created_at'], name='datacred_v2_estado_idx'),
        ]
        permissions = [
            ('can_force_datacredito_refresh', 'Puede forzar una consulta DataCrédito'),
        ]

    @property
    def reutilizable(self):
        return (
            self.estado in {self.Estado.EXITOSO, self.Estado.SIN_INFORMACION}
            and self.vigente_hasta > timezone.now()
        )

    def save(self, *args, **kwargs):
        if self.pk:
            estado_anterior = type(self).objects.filter(pk=self.pk).values_list(
                'estado', flat=True
            ).first()
            if estado_anterior and estado_anterior != self.Estado.EN_PROCESO:
                raise ValidationError('Un snapshot finalizado es inmutable.')
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.servicio} {self.documento_enmascarado} {self.estado}'
