import uuid

from django.conf import settings
from django.db import models


class ConsultaDatacreditoSnapshot(models.Model):
    SERVICIO_DECISOR = 'decisor'
    SERVICIO_HISTORIAL = 'historial'
    SERVICIOS = (
        (SERVICIO_DECISOR, 'MiDecisor'),
        (SERVICIO_HISTORIAL, 'Historia de Credito'),
    )

    PROVEEDOR_DATACREDITO_REAL = 'datacredito_real'
    PROVEEDOR_MOCK = 'mock'
    PROVEEDORES = (
        (PROVEEDOR_DATACREDITO_REAL, 'DataCredito real'),
        (PROVEEDOR_MOCK, 'Mock'),
    )

    SOURCE_CONSULTA_REAL = 'CONSULTA_REAL'
    SOURCE_MOCK = 'MOCK'
    SOURCE_DIAGNOSTICO = 'DIAGNOSTICO'
    SOURCES = (
        (SOURCE_CONSULTA_REAL, 'Consulta real'),
        (SOURCE_MOCK, 'Mock'),
        (SOURCE_DIAGNOSTICO, 'Diagnostico'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    servicio = models.CharField(max_length=20, choices=SERVICIOS, db_index=True)
    ambiente = models.CharField(max_length=20, db_index=True)
    proveedor = models.CharField(max_length=40, choices=PROVEEDORES, default=PROVEEDOR_DATACREDITO_REAL)
    tipo_documento = models.CharField(max_length=20)
    request_fingerprint = models.CharField(max_length=128, db_index=True)
    documento_hash = models.CharField(max_length=128, db_index=True)
    documento_enmascarado = models.CharField(max_length=40)
    estado_normalizado = models.CharField(max_length=80, db_index=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    codigo_funcional = models.CharField(max_length=60, blank=True)
    proveedor_respondio = models.BooleanField(default=False)
    consulta_procesada = models.BooleanField(default=False)
    con_informacion = models.BooleanField(null=True, blank=True)
    utilizable_para_score = models.BooleanField(default=False)
    requiere_revision_manual = models.BooleanField(default=True)
    requiere_revision_cumplimiento = models.BooleanField(default=False)
    resultado_normalizado = models.JSONField(default=dict, blank=True)
    consulted_at = models.DateTimeField(db_index=True)
    vigente_hasta = models.DateTimeField(db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='snapshots_datacredito_creados',
    )
    request_id = models.CharField(max_length=120, blank=True)
    source = models.CharField(max_length=30, choices=SOURCES, default=SOURCE_CONSULTA_REAL, db_index=True)
    autorizacion_id = models.CharField(max_length=40, blank=True)
    autorizacion_version_texto = models.CharField(max_length=80, blank=True)
    autorizacion_texto_hash = models.CharField(max_length=128, blank=True)
    autorizacion_accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-consulted_at',)
        indexes = (
            models.Index(
                fields=['ambiente', 'servicio', 'request_fingerprint', 'source', 'vigente_hasta'],
                name='datacred_snap_reuse_idx',
            ),
            models.Index(fields=['documento_hash', 'consulted_at'], name='datacred_snap_doc_idx'),
            models.Index(fields=['autorizacion_id'], name='datacred_snap_auth_idx'),
        )
        permissions = (
            ('can_force_datacredito_refresh', 'Puede forzar nueva consulta DataCredito'),
        )
        verbose_name = 'snapshot DataCredito'
        verbose_name_plural = 'snapshots DataCredito'

    def __str__(self):
        return f'{self.servicio} {self.documento_enmascarado} {self.estado_normalizado}'
