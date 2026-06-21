from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from libranza.escenarios_credito import EscenarioCreditoLibranza


TAMANO_MAXIMO_DOCUMENTO_BYTES = 10 * 1024 * 1024
TIPOS_CONTENIDO_DOCUMENTO_PERMITIDOS = {
    'application/pdf',
    'image/jpeg',
    'image/png',
}


class ContractorOrganization(models.Model):
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=80, unique=True)
    subdomain = models.SlugField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Organizacion contratista'
        verbose_name_plural = 'Organizaciones contratistas'
        indexes = [
            models.Index(fields=['subdomain', 'is_active'], name='contractor_subdomain_idx'),
            models.Index(fields=['slug'], name='contractor_slug_idx'),
        ]

    def clean_fields(self, exclude=None):
        self.subdomain = (self.subdomain or '').strip().lower()
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if not self.subdomain:
            raise ValidationError({'subdomain': 'El subdominio es obligatorio.'})

    def __str__(self):
        return f'{self.name} ({self.subdomain})'


class ContractorProfile(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        MANAGER = 'manager', 'Manager'
        OPERATOR = 'operator', 'Operator'
        VIEWER = 'viewer', 'Viewer'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contractor_profiles',
    )
    organization = models.ForeignKey(
        ContractorOrganization,
        on_delete=models.CASCADE,
        related_name='profiles',
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['organization__name', 'user_id']
        verbose_name = 'Perfil contratista'
        verbose_name_plural = 'Perfiles contratistas'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'organization'],
                name='unique_contractor_profile_user_org',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active'], name='contractor_profile_user_idx'),
            models.Index(fields=['organization', 'is_active'], name='contractor_profile_org_idx'),
        ]

    def __str__(self):
        return f'{self.user_id} - {self.organization_id} ({self.role})'


class ContractorBranding(models.Model):
    organization = models.OneToOneField(
        ContractorOrganization,
        on_delete=models.CASCADE,
        related_name='branding',
    )
    display_name = models.CharField(max_length=160)
    logo = models.ImageField(
        upload_to='contractors/branding/logos/',
        null=True,
        blank=True,
    )
    primary_color = models.CharField(max_length=20, default='#0d6efd')
    secondary_color = models.CharField(max_length=20, default='#6c757d')
    support_email = models.EmailField(blank=True)
    landing_copy = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['organization__name']
        verbose_name = 'Marca contratista'
        verbose_name_plural = 'Marcas contratistas'
        indexes = [
            models.Index(fields=['organization', 'is_active'], name='contractor_branding_org_idx'),
        ]

    def __str__(self):
        return f'{self.display_name} ({self.organization_id})'


class ContractorProductConfig(models.Model):
    class ProductType(models.TextChoices):
        CONTRACTOR_CREDIT = 'contractor_credit', 'Credito contratista'

    organization = models.ForeignKey(
        ContractorOrganization,
        on_delete=models.CASCADE,
        related_name='product_configs',
    )
    product_type = models.CharField(
        max_length=40,
        choices=ProductType.choices,
        default=ProductType.CONTRACTOR_CREDIT,
    )
    min_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    max_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    min_term_months = models.PositiveSmallIntegerField(default=1)
    max_term_months = models.PositiveSmallIntegerField()
    monthly_rate = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0000'))],
        help_text='Tasa mensual porcentual.',
    )
    commission_rate = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('0.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
        help_text='Comision porcentual sobre el monto solicitado.',
    )
    commission_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    vat_rate = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('19.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
        help_text='IVA porcentual sobre la comision.',
    )
    allows_second_credit = models.BooleanField(default=False)
    allows_portfolio_takeover = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['organization__name', 'product_type']
        verbose_name = 'Configuracion de producto contratista'
        verbose_name_plural = 'Configuraciones de producto contratista'
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'product_type'],
                condition=models.Q(is_active=True),
                name='unique_active_contractor_product_config',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'product_type', 'is_active'], name='contractor_product_cfg_idx'),
        ]

    def __str__(self):
        return f'{self.organization_id} - {self.product_type}'

    def clean(self):
        super().clean()
        errores = {}

        if self.min_amount is not None and self.max_amount is not None and self.min_amount > self.max_amount:
            errores['min_amount'] = 'El monto minimo no puede ser mayor que el monto maximo.'

        if (
            self.min_term_months is not None
            and self.max_term_months is not None
            and self.min_term_months > self.max_term_months
        ):
            errores['min_term_months'] = 'El plazo minimo no puede ser mayor que el plazo maximo.'

        campos_no_negativos = {
            'monthly_rate': 'La tasa mensual no puede ser negativa.',
            'commission_rate': 'La comision porcentual no puede ser negativa.',
            'commission_amount': 'La comision fija no puede ser negativa.',
            'vat_rate': 'El IVA no puede ser negativo.',
        }
        for campo, mensaje in campos_no_negativos.items():
            valor = getattr(self, campo)
            if valor is not None and valor < 0:
                errores[campo] = mensaje

        if errores:
            raise ValidationError(errores)


class ConfiguracionPortalContratistas(models.Model):
    nombre_visible = models.CharField(max_length=160)
    host = models.CharField(max_length=180)
    slug = models.SlugField(max_length=80, unique=True)
    activo = models.BooleanField(default=True)
    color_primario = models.CharField(max_length=20, default='#0d6efd')
    color_secundario = models.CharField(max_length=20, default='#6c757d')
    logo = models.ImageField(
        upload_to='contractors/portal/logos/',
        null=True,
        blank=True,
    )
    correo_soporte = models.EmailField(blank=True)
    texto_landing = models.TextField(blank=True)
    monto_minimo = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    monto_maximo = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    plazo_minimo_meses = models.PositiveSmallIntegerField(default=1)
    plazo_maximo_meses = models.PositiveSmallIntegerField()
    tasa_mensual = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0000'))],
    )
    tasa_comision = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('0.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
    )
    comision_fija = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    tasa_iva = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('19.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
    )
    tasa_fondo_garantia = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('2.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
    )
    iva_fondo_garantia = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal('19.0000'),
        validators=[MinValueValidator(Decimal('0.0000'))],
    )
    fondo_garantia_incluye_iva = models.BooleanField(default=True)
    factor_seguro_vida = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        default=Decimal('0.003711'),
        validators=[MinValueValidator(Decimal('0.000000'))],
    )
    seguro_vida_financiado = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['host']
        verbose_name = 'Configuracion portal contratistas'
        verbose_name_plural = 'Configuraciones portal contratistas'
        constraints = [
            models.UniqueConstraint(
                fields=['host'],
                condition=models.Q(activo=True),
                name='unique_active_contractor_portal_host',
            ),
        ]
        indexes = [
            models.Index(fields=['host', 'activo'], name='contr_portal_host_idx'),
            models.Index(fields=['slug', 'activo'], name='contr_portal_slug_idx'),
        ]

    def clean_fields(self, exclude=None):
        self.host = self.normalizar_host(self.host)
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        errores = {}

        if not self.host:
            errores['host'] = 'El host del portal es obligatorio.'

        if self.monto_minimo is not None and self.monto_maximo is not None and self.monto_minimo > self.monto_maximo:
            errores['monto_minimo'] = 'El monto minimo no puede ser mayor que el monto maximo.'

        if (
            self.plazo_minimo_meses is not None
            and self.plazo_maximo_meses is not None
            and self.plazo_minimo_meses > self.plazo_maximo_meses
        ):
            errores['plazo_minimo_meses'] = 'El plazo minimo no puede ser mayor que el plazo maximo.'

        campos_no_negativos = {
            'tasa_mensual': 'La tasa mensual no puede ser negativa.',
            'tasa_comision': 'La comision porcentual no puede ser negativa.',
            'comision_fija': 'La comision fija no puede ser negativa.',
            'tasa_iva': 'El IVA no puede ser negativo.',
            'tasa_fondo_garantia': 'La tasa del fondo de garantia no puede ser negativa.',
            'iva_fondo_garantia': 'El IVA del fondo de garantia no puede ser negativo.',
            'factor_seguro_vida': 'El factor del seguro de vida no puede ser negativo.',
        }
        for campo, mensaje in campos_no_negativos.items():
            valor = getattr(self, campo)
            if valor is not None and valor < 0:
                errores[campo] = mensaje

        if errores:
            raise ValidationError(errores)

    @staticmethod
    def normalizar_host(host):
        host = (host or '').strip().lower()
        host = host.removeprefix('https://').removeprefix('http://')
        return host.split('/', 1)[0].split(':', 1)[0]

    @property
    def is_active(self):
        return self.activo

    @property
    def min_amount(self):
        return self.monto_minimo

    @property
    def max_amount(self):
        return self.monto_maximo

    @property
    def min_term_months(self):
        return self.plazo_minimo_meses

    @property
    def max_term_months(self):
        return self.plazo_maximo_meses

    @property
    def monthly_rate(self):
        return self.tasa_mensual

    @property
    def commission_rate(self):
        return self.tasa_comision

    @property
    def commission_amount(self):
        return self.comision_fija

    @property
    def vat_rate(self):
        return self.tasa_iva

    @property
    def product_type(self):
        return ContractorProductConfig.ProductType.CONTRACTOR_CREDIT

    def __str__(self):
        return f'{self.nombre_visible} ({self.host})'


class ContractorApplication(models.Model):
    class Estado(models.TextChoices):
        RECIBIDA = 'RECIBIDA', 'Recibida'
        EN_REVISION = 'EN_REVISION', 'En revision'
        RECHAZADA = 'RECHAZADA', 'Rechazada'
        CONVERTIDA = 'CONVERTIDA', 'Convertida'

    EscenarioCredito = EscenarioCreditoLibranza

    organization = models.ForeignKey(
        ContractorOrganization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='applications',
    )
    configuracion_portal = models.ForeignKey(
        ConfiguracionPortalContratistas,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='solicitudes',
    )
    product_config = models.ForeignKey(
        ContractorProductConfig,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='applications',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='solicitudes_contratista',
    )
    status = models.CharField(max_length=20, choices=Estado.choices, default=Estado.RECIBIDA)
    escenario_credito = models.CharField(
        max_length=30,
        choices=EscenarioCredito.choices,
        default=EscenarioCredito.NUEVO_CREDITO,
    )
    requested_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    term_months = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    estimated_monthly_payment = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    simulation_payload = models.JSONField(default=dict, blank=True)
    document_type = models.CharField(max_length=30)
    document_number = models.CharField(max_length=40)
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=40)
    email = models.EmailField()
    address = models.CharField(max_length=255, blank=True)
    accepted_terms = models.BooleanField(default=False)
    source_subdomain = models.SlugField(max_length=80)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    credito = models.ForeignKey(
        'gestion_creditos.Credito',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contractor_applications',
    )
    revisado_en = models.DateTimeField(null=True, blank=True)
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_contratista_revisadas',
    )
    notas_revision = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Pre-solicitud contratista'
        verbose_name_plural = 'Pre-solicitudes contratistas'
        permissions = [
            ('can_review_contractor_application', 'Puede revisar pre-solicitudes contratistas'),
            ('can_evaluate_contractor_predecision', 'Puede evaluar predecision de prestadores'),
            ('can_originate_contractor_credit', 'Puede originar credito de prestadores'),
        ]
        indexes = [
            models.Index(fields=['organization', 'status', 'created_at'], name='contractor_app_org_status_idx'),
            models.Index(fields=['configuracion_portal', 'usuario', 'created_at'], name='contractor_app_portal_user_idx'),
            models.Index(fields=['document_number'], name='contractor_app_doc_idx'),
        ]

    def clean_fields(self, exclude=None):
        self.source_subdomain = (self.source_subdomain or '').strip().lower()
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        errores = {}

        if self.organization_id and self.organization and not self.organization.is_active:
            errores['organization'] = 'La organizacion contratista debe estar activa.'

        if self.configuracion_portal_id and self.configuracion_portal and not self.configuracion_portal.activo:
            errores['configuracion_portal'] = 'La configuracion del portal debe estar activa.'

        if not self.organization_id and not self.configuracion_portal_id:
            errores['configuracion_portal'] = 'La solicitud debe tener configuracion de portal u organizacion legacy.'

        if self.product_config_id and self.organization_id:
            if self.product_config.organization_id != self.organization_id:
                errores['product_config'] = 'La configuracion de producto no pertenece a la organizacion.'

        if not self.accepted_terms:
            errores['accepted_terms'] = 'Debe aceptar terminos y condiciones.'

        if self.credito_id and self.status != self.Estado.CONVERTIDA:
            errores['credito'] = 'Solo una solicitud convertida puede tener credito vinculado.'

        if errores:
            raise ValidationError(errores)

    def __str__(self):
        return f'{self.document_number} - {self.organization_id} ({self.status})'


class SimulacionPrestador(models.Model):
    solicitud = models.ForeignKey(
        ContractorApplication,
        on_delete=models.PROTECT,
        related_name='simulaciones_prestador',
    )
    monto_solicitado = models.DecimalField(max_digits=14, decimal_places=2)
    plazo_meses = models.PositiveSmallIntegerField()
    tasa_mensual = models.DecimalField(max_digits=7, decimal_places=4)
    tea = models.DecimalField(max_digits=9, decimal_places=4)
    costo_originacion = models.DecimalField(max_digits=14, decimal_places=2)
    iva_costo_originacion = models.DecimalField(max_digits=14, decimal_places=2)
    fondo_garantia_total = models.DecimalField(max_digits=14, decimal_places=2)
    fondo_garantia_base = models.DecimalField(max_digits=14, decimal_places=2)
    fondo_garantia_iva = models.DecimalField(max_digits=14, decimal_places=2)
    seguro_vida = models.DecimalField(max_digits=14, decimal_places=2)
    capital_total_financiado = models.DecimalField(max_digits=14, decimal_places=2)
    cuota_mensual = models.DecimalField(max_digits=14, decimal_places=2)
    intereses_estimados = models.DecimalField(max_digits=14, decimal_places=2)
    total_estimado = models.DecimalField(max_digits=14, decimal_places=2)
    version_politica = models.CharField(max_length=40)
    aceptada = models.BooleanField(default=False)
    aceptada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='simulaciones_prestador_aceptadas',
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Simulacion prestador'
        verbose_name_plural = 'Simulaciones prestador'
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud'],
                condition=models.Q(aceptada=True),
                name='unique_simulacion_prestador_aceptada',
            ),
        ]
        indexes = [
            models.Index(fields=['solicitud', 'aceptada'], name='sim_prest_solic_acept_idx'),
            models.Index(fields=['created_at'], name='sim_prest_created_idx'),
        ]

    def __str__(self):
        return f'Simulacion {self.id} solicitud {self.solicitud_id}'


class PredecisionPrestadorAudit(models.Model):
    solicitud = models.ForeignKey(
        ContractorApplication,
        on_delete=models.PROTECT,
        related_name='auditorias_predecision',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias_predecision_prestador',
    )
    escenario_credito = models.CharField(max_length=30)
    decision = models.CharField(max_length=40)
    eligible = models.BooleanField(default=False)
    requiere_revision_manual = models.BooleanField(default=False)
    monto_maximo_sugerido = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    plazo_maximo_sugerido = models.PositiveSmallIntegerField(default=0)
    score_status = models.CharField(max_length=40, blank=True)
    score_final = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    score_banda = models.CharField(max_length=40, blank=True)
    score_version_configuracion = models.CharField(max_length=80, blank=True)
    datacredito_status = models.CharField(max_length=40, blank=True)
    datacredito_fuente = models.CharField(max_length=60, blank=True)
    datacredito_mora_severa = models.BooleanField(default=False)
    datacredito_mora_actual = models.BooleanField(default=False)
    autorizacion_datacredito = models.ForeignKey(
        'AutorizacionConsultaDatacreditoPrestador',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='auditorias_predecision',
    )
    snapshot_decisor = models.ForeignKey(
        'integrations.ConsultaDatacreditoSnapshot',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='auditorias_prestador_decisor',
    )
    snapshot_historial = models.ForeignKey(
        'integrations.ConsultaDatacreditoSnapshot',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='auditorias_prestador_historial',
    )
    datacredito_modo = models.CharField(max_length=40, blank=True)
    decisor_reutilizado = models.BooleanField(default=False)
    historial_reutilizado = models.BooleanField(default=False)
    decisor_consultado = models.BooleanField(default=False)
    historial_consultado = models.BooleanField(default=False)
    justificacion_consulta_forzada = models.CharField(max_length=500, blank=True)
    capacidad_status = models.CharField(max_length=40, blank=True)
    riesgo_status = models.CharField(max_length=40, blank=True)
    bloqueos = models.JSONField(default=list, blank=True)
    advertencias = models.JSONField(default=list, blank=True)
    razones = models.JSONField(default=list, blank=True)
    resultado_sanitizado = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=120, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Auditoria de predecision prestador'
        verbose_name_plural = 'Auditorias de predecision prestador'
        permissions = [
            ('can_view_contractor_risk_queue', 'Puede ver bandeja de riesgo de prestadores'),
            ('can_run_contractor_datacredito_evaluation', 'Puede evaluar DataCredito de prestadores'),
            ('can_force_contractor_datacredito_refresh', 'Puede forzar consulta DataCredito de prestadores'),
            ('can_register_uat_datacredito_authorization', 'Puede registrar autorizacion DataCredito UAT de prestadores'),
        ]
        indexes = [
            models.Index(fields=['solicitud', 'created_at'], name='predec_audit_solicitud_idx'),
            models.Index(fields=['decision', 'created_at'], name='predec_audit_decision_idx'),
            models.Index(fields=['datacredito_status'], name='predec_audit_datacred_idx'),
            models.Index(fields=['score_banda'], name='predec_audit_score_idx'),
        ]

    def __str__(self):
        return f'Predecision solicitud {self.solicitud_id} - {self.decision}'


class AutorizacionConsultaDatacreditoPrestador(models.Model):
    class Fuente(models.TextChoices):
        FORMULARIO_PUBLICO = 'FORMULARIO_PUBLICO', 'Formulario publico'
        STAFF_UAT = 'STAFF_UAT', 'Staff UAT'
        MIGRACION_CONTROLADA = 'MIGRACION_CONTROLADA', 'Migracion controlada'

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
    autorizado = models.BooleanField(default=True)
    version_texto = models.CharField(max_length=80)
    texto_hash = models.CharField(max_length=128)
    finalidad = models.CharField(max_length=240)
    accepted_at = models.DateTimeField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=40, choices=Fuente.choices)
    justificacion = models.CharField(max_length=500, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-accepted_at', '-id']
        verbose_name = 'Autorizacion consulta DataCredito prestador'
        verbose_name_plural = 'Autorizaciones consulta DataCredito prestador'
        indexes = [
            models.Index(fields=['solicitud', 'version_texto', 'revoked_at'], name='auth_dc_solic_version_idx'),
            models.Index(fields=['usuario', 'accepted_at'], name='auth_dc_usuario_fecha_idx'),
            models.Index(fields=['source', 'accepted_at'], name='auth_dc_source_fecha_idx'),
        ]

    def __str__(self):
        estado = 'revocada' if self.revoked_at else 'vigente'
        return f'Autorizacion DataCredito solicitud {self.solicitud_id} v{self.version_texto} ({estado})'


class NovedadPagadorPrestador(models.Model):
    class TipoNovedad(models.TextChoices):
        CREDITO_PRESTADOR_EN_REVISION = 'CREDITO_PRESTADOR_EN_REVISION', 'Credito prestador en revision'

    class Estado(models.TextChoices):
        REGISTRADA = 'REGISTRADA', 'Registrada'
        ENVIADA = 'ENVIADA', 'Enviada'
        ERROR = 'ERROR', 'Error'

    credito = models.ForeignKey(
        'gestion_creditos.Credito',
        on_delete=models.PROTECT,
        related_name='novedades_pagador_prestador',
    )
    solicitud = models.ForeignKey(
        ContractorApplication,
        on_delete=models.PROTECT,
        related_name='novedades_pagador',
    )
    empresa = models.ForeignKey(
        'gestion_creditos.Empresa',
        on_delete=models.PROTECT,
        related_name='novedades_pagador_prestador',
    )
    tipo = models.CharField(
        max_length=60,
        choices=TipoNovedad.choices,
        default=TipoNovedad.CREDITO_PRESTADOR_EN_REVISION,
    )
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.REGISTRADA,
    )
    destinatarios = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='novedades_pagador_prestador_creadas',
    )
    request_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Novedad pagador prestador'
        verbose_name_plural = 'Novedades pagador prestador'
        permissions = [
            ('can_notify_contractor_payer', 'Puede notificar novedad de prestador al pagador'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['credito', 'tipo'],
                name='unique_novedad_pagador_prestador_credito_tipo',
            ),
        ]
        indexes = [
            models.Index(fields=['empresa', 'created_at'], name='nov_pag_prest_emp_fecha_idx'),
            models.Index(fields=['estado', 'created_at'], name='nov_pag_prest_estado_idx'),
        ]

    def __str__(self):
        return f'{self.tipo} - {self.credito_id} ({self.estado})'


class TimelinePrestador(models.Model):
    class TipoEvento(models.TextChoices):
        SOLICITUD_CREADA = 'SOLICITUD_CREADA', 'Solicitud creada'
        DOCUMENTOS_CARGADOS = 'DOCUMENTOS_CARGADOS', 'Documentos cargados'
        ANALISIS_IA_CONTRATO = 'ANALISIS_IA_CONTRATO', 'Analisis IA contrato'
        PREDECISION_EJECUTADA = 'PREDECISION_EJECUTADA', 'Predecision ejecutada'
        AUDITORIA_PREDECISION_CREADA = 'AUDITORIA_PREDECISION_CREADA', 'Auditoria predecision creada'
        ORIGINADO_EN_REVISION = 'ORIGINADO_EN_REVISION', 'Originado en revision'
        NOVEDAD_PAGADOR_REGISTRADA = 'NOVEDAD_PAGADOR_REGISTRADA', 'Novedad pagador registrada'
        FIRMA_V2_PREPARADA = 'FIRMA_V2_PREPARADA', 'Firma V2 preparada'
        DRAFT_FIRMA_V2_CREADO = 'DRAFT_FIRMA_V2_CREADO', 'Draft firma V2 creado'
        SIMULACION_ACEPTADA = 'SIMULACION_ACEPTADA', 'Simulacion aceptada'
        DATACREDITO_EVALUACION_INICIADA = 'DATACREDITO_EVALUACION_INICIADA', 'DataCredito evaluacion iniciada'
        DATACREDITO_SNAPSHOT_REUTILIZADO = 'DATACREDITO_SNAPSHOT_REUTILIZADO', 'DataCredito snapshot reutilizado'
        DATACREDITO_CONSULTA_REALIZADA = 'DATACREDITO_CONSULTA_REALIZADA', 'DataCredito consulta realizada'
        DATACREDITO_EVALUACION_COMPLETADA = 'DATACREDITO_EVALUACION_COMPLETADA', 'DataCredito evaluacion completada'
        DATACREDITO_EVALUACION_FALLIDA = 'DATACREDITO_EVALUACION_FALLIDA', 'DataCredito evaluacion fallida'
        DATACREDITO_CONSULTA_FORZADA = 'DATACREDITO_CONSULTA_FORZADA', 'DataCredito consulta forzada'
        DATACREDITO_AUTORIZACION_ACEPTADA = 'DATACREDITO_AUTORIZACION_ACEPTADA', 'DataCredito autorizacion aceptada'
        DATACREDITO_AUTORIZACION_REVOCADA = 'DATACREDITO_AUTORIZACION_REVOCADA', 'DataCredito autorizacion revocada'
        DATACREDITO_AUTORIZACION_UAT_REGISTRADA = 'DATACREDITO_AUTORIZACION_UAT_REGISTRADA', 'DataCredito autorizacion UAT registrada'
        DATACREDITO_CONSULTA_BLOQUEADA_SIN_AUTORIZACION = 'DATACREDITO_CONSULTA_BLOQUEADA_SIN_AUTORIZACION', 'DataCredito consulta bloqueada sin autorizacion'

    solicitud = models.ForeignKey(
        ContractorApplication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timeline_operativo',
    )
    credito = models.ForeignKey(
        'gestion_creditos.Credito',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timeline_prestador',
    )
    tipo_evento = models.CharField(max_length=60, choices=TipoEvento.choices)
    titulo = models.CharField(max_length=160)
    descripcion = models.TextField(blank=True)
    estado_resultante = models.CharField(max_length=60, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timeline_prestadores',
    )
    request_id = models.CharField(max_length=120, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Timeline operativo prestador'
        verbose_name_plural = 'Timeline operativo prestadores'
        indexes = [
            models.Index(fields=['solicitud', '-created_at'], name='tl_prest_solicitud_fecha_idx'),
            models.Index(fields=['credito', '-created_at'], name='tl_prest_credito_fecha_idx'),
            models.Index(fields=['tipo_evento', '-created_at'], name='tl_prest_evento_fecha_idx'),
        ]

    def __str__(self):
        referencia = self.solicitud_id or self.credito_id or '-'
        return f'{self.tipo_evento} - {referencia}'


class ContractorApplicationDocument(models.Model):
    class TipoDocumento(models.TextChoices):
        CONTRATO_ACTUAL = 'contrato_actual', 'Contrato actual'
        DOCUMENTO_IDENTIDAD_FRONTAL = 'documento_identidad_frontal', 'Documento identidad frontal'
        DOCUMENTO_IDENTIDAD_REVERSO = 'documento_identidad_reverso', 'Documento identidad reverso'
        CERTIFICADO_BANCARIO = 'certificado_bancario', 'Certificado bancario'

    class Estado(models.TextChoices):
        RECIBIDO = 'RECIBIDO', 'Recibido'
        APROBADO = 'APROBADO', 'Aprobado'
        RECHAZADO = 'RECHAZADO', 'Rechazado'

    application = models.ForeignKey(
        ContractorApplication,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    document_type = models.CharField(max_length=60, choices=TipoDocumento.choices)
    file = models.FileField(upload_to='contractors/applications/documents/')
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    file_size = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(max_length=20, choices=Estado.choices, default=Estado.RECIBIDO)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_contractor_documents',
    )
    review_notes = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Documento de pre-solicitud contratista'
        verbose_name_plural = 'Documentos de pre-solicitud contratista'
        permissions = [
            ('can_review_contractor_document', 'Puede revisar documentos de pre-solicitud contratista'),
        ]
        indexes = [
            models.Index(fields=['application', 'document_type'], name='contractor_doc_app_type_idx'),
            models.Index(fields=['status', 'uploaded_at'], name='contractor_doc_status_idx'),
        ]

    def clean(self):
        super().clean()
        errores = {}

        if self.application_id and self.application:
            organizacion_inactiva = (
                self.application.organization_id
                and not self.application.organization.is_active
            )
            portal_inactivo = (
                self.application.configuracion_portal_id
                and not self.application.configuracion_portal.activo
            )
            if organizacion_inactiva or portal_inactivo:
                errores['application'] = 'La solicitud debe pertenecer a un portal u organizacion activa.'

        if self.application_id and self.application and self.application.status not in {
            ContractorApplication.Estado.RECIBIDA,
            ContractorApplication.Estado.EN_REVISION,
        }:
            errores['application'] = 'La solicitud no permite carga de documentos en su estado actual.'

        if not self.original_filename:
            errores['original_filename'] = 'El nombre original del archivo es obligatorio.'
        elif not Path(self.original_filename).suffix:
            errores['original_filename'] = 'El archivo debe tener extension.'

        if not self.content_type:
            errores['content_type'] = 'El tipo de contenido del archivo es obligatorio.'

        if self.content_type and self.content_type not in TIPOS_CONTENIDO_DOCUMENTO_PERMITIDOS:
            errores['content_type'] = 'El tipo de archivo no esta permitido.'

        if self.file_size is not None:
            if self.file_size <= 0:
                errores['file_size'] = 'El tamano del archivo debe ser mayor a cero.'
            elif self.file_size > TAMANO_MAXIMO_DOCUMENTO_BYTES:
                errores['file_size'] = 'El archivo supera el tamano maximo permitido.'
        else:
            errores['file_size'] = 'El tamano del archivo es obligatorio.'

        if errores:
            raise ValidationError(errores)

    def __str__(self):
        return f'{self.document_type} - solicitud {self.application_id}'


class InformacionLaboralSolicitudContratista(models.Model):
    class TipoContrato(models.TextChoices):
        PRESTACION_SERVICIOS = 'PRESTACION_SERVICIOS', 'Prestacion de servicios'
        LABORAL = 'LABORAL', 'Laboral'
        OTRO = 'OTRO', 'Otro'

    solicitud = models.OneToOneField(
        ContractorApplication,
        on_delete=models.CASCADE,
        related_name='informacion_laboral',
    )
    empresa = models.ForeignKey(
        'gestion_creditos.Empresa',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='solicitudes_contratista_laborales',
    )
    cargo = models.CharField(max_length=160)
    tipo_contrato = models.CharField(max_length=30, choices=TipoContrato.choices)
    fecha_inicio_contrato = models.DateField()
    fecha_fin_contrato = models.DateField()
    valor_total_contrato = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    valor_pagado_contrato = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    valor_pendiente_cobrar = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    empresa_contratante_nombre = models.CharField(max_length=180, blank=True)
    empresa_contratante_nit = models.CharField(max_length=40, blank=True)
    pagador_nombre = models.CharField(max_length=160, blank=True)
    pagador_email = models.EmailField(blank=True)
    pagador_telefono = models.CharField(max_length=40, blank=True)
    observaciones = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Informacion laboral de solicitud contratista'
        verbose_name_plural = 'Informacion laboral de solicitudes contratistas'
        indexes = [
            models.Index(fields=['tipo_contrato'], name='contr_info_tipo_idx'),
            models.Index(fields=['empresa'], name='contr_info_empresa_fk_idx'),
            models.Index(fields=['empresa_contratante_nombre'], name='contr_info_empresa_idx'),
            models.Index(fields=['fecha_fin_contrato'], name='contr_info_fecha_fin_idx'),
        ]

    def clean(self):
        super().clean()
        errores = {}

        if self.solicitud_id and self.solicitud:
            organizacion_inactiva = (
                self.solicitud.organization_id
                and not self.solicitud.organization.is_active
            )
            portal_inactivo = (
                self.solicitud.configuracion_portal_id
                and not self.solicitud.configuracion_portal.activo
            )
            if organizacion_inactiva or portal_inactivo:
                errores['solicitud'] = 'La solicitud debe pertenecer a un portal u organizacion activa.'

        if not self.cargo:
            errores['cargo'] = 'El cargo es obligatorio.'

        if not self.tipo_contrato:
            errores['tipo_contrato'] = 'El tipo de contrato es obligatorio.'

        if self.empresa_id and self.empresa and not self.empresa.permite_libranza:
            errores['empresa'] = 'La empresa seleccionada debe tener convenio activo de libranza.'

        if not self.empresa_id and not self.empresa_contratante_nombre:
            errores['empresa'] = 'La empresa contratante es obligatoria.'

        if self.fecha_inicio_contrato and self.fecha_fin_contrato:
            if self.fecha_fin_contrato < self.fecha_inicio_contrato:
                errores['fecha_fin_contrato'] = 'La fecha de fin no puede ser menor que la fecha de inicio.'

        campos_no_negativos = {
            'valor_total_contrato': 'El valor total del contrato no puede ser negativo.',
            'valor_pagado_contrato': 'El valor pagado del contrato no puede ser negativo.',
            'valor_pendiente_cobrar': 'El valor pendiente por cobrar no puede ser negativo.',
        }
        for campo, mensaje in campos_no_negativos.items():
            valor = getattr(self, campo)
            if valor is not None and valor < 0:
                errores[campo] = mensaje

        if (
            self.valor_total_contrato is not None
            and self.valor_pagado_contrato is not None
            and self.valor_pendiente_cobrar is not None
            and self.valor_pagado_contrato + self.valor_pendiente_cobrar > self.valor_total_contrato
        ):
            errores['valor_pendiente_cobrar'] = (
                'La suma de valor pagado y valor pendiente no puede superar el valor total del contrato.'
            )

        if errores:
            raise ValidationError(errores)

    def __str__(self):
        return f'{self.empresa_contratante_nombre} - solicitud {self.solicitud_id}'
