from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib import messages

from contractors.models import (
    AutorizacionConsultaDatacreditoPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorBranding,
    ContractorOrganization,
    ContractorProductConfig,
    ContractorProfile,
    ConfiguracionPortalContratistas,
    InformacionLaboralSolicitudContratista,
    NovedadPagadorPrestador,
    PredecisionPrestadorAudit,
    SimulacionPrestador,
    TimelinePrestador,
)
from contractors.services.revision import (
    aprobar_documento_solicitud,
    marcar_solicitud_en_revision,
    rechazar_documento_solicitud,
    rechazar_solicitud_contratista,
)
from contractors.services.evaluacion_formal import evaluar_formalmente_solicitud_prestador


class AdminContratistasBase(admin.ModelAdmin):
    readonly_fields = ('created_at', 'updated_at')
    actions = None

    def get_actions(self, request):
        acciones = super().get_actions(request)
        acciones.pop('delete_selected', None)
        return acciones


@admin.register(ConfiguracionPortalContratistas)
class ConfiguracionPortalContratistasAdmin(AdminContratistasBase):
    list_display = ('nombre_visible', 'host', 'slug', 'activo', 'monto_minimo', 'monto_maximo', 'updated_at')
    search_fields = ('nombre_visible', 'host', 'slug', 'correo_soporte')
    list_filter = ('activo',)
    fieldsets = (
        ('Portal unico', {
            'fields': ('nombre_visible', 'host', 'slug', 'activo'),
        }),
        ('Marca', {
            'fields': ('logo', 'color_primario', 'color_secundario', 'correo_soporte', 'texto_landing'),
        }),
        ('Condiciones financieras', {
            'fields': (
                'monto_minimo',
                'monto_maximo',
                'plazo_minimo_meses',
                'plazo_maximo_meses',
                'tasa_mensual',
                'tasa_comision',
                'comision_fija',
                'tasa_iva',
                'tasa_fondo_garantia',
                'iva_fondo_garantia',
                'fondo_garantia_incluye_iva',
                'factor_seguro_vida',
                'seguro_vida_financiado',
            ),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorOrganization)
class OrganizacionContratistaAdmin(AdminContratistasBase):
    list_display = ('name', 'slug', 'subdomain', 'is_active', 'created_at')
    search_fields = ('name', 'slug', 'subdomain')
    list_filter = ('is_active',)
    fieldsets = (
        ('Identificacion', {
            'fields': ('name', 'slug', 'subdomain'),
        }),
        ('Estado', {
            'fields': ('is_active',),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorBranding)
class MarcaContratistaAdmin(AdminContratistasBase):
    list_display = ('display_name', 'organization', 'is_active', 'support_email', 'updated_at')
    search_fields = ('display_name', 'organization__name', 'support_email')
    list_filter = ('is_active', 'organization')
    fieldsets = (
        ('Organizacion', {
            'fields': ('organization', 'is_active'),
        }),
        ('Marca', {
            'fields': ('display_name', 'logo', 'primary_color', 'secondary_color'),
        }),
        ('Contenido publico', {
            'fields': ('landing_copy', 'support_email'),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorProductConfig)
class ConfiguracionProductoContratistaAdmin(AdminContratistasBase):
    list_display = (
        'organization',
        'product_type',
        'min_amount',
        'max_amount',
        'min_term_months',
        'max_term_months',
        'monthly_rate',
        'is_active',
    )
    search_fields = ('organization__name', 'organization__subdomain', 'product_type')
    list_filter = ('is_active', 'product_type', 'allows_second_credit', 'allows_portfolio_takeover')
    fieldsets = (
        ('Organizacion y producto', {
            'fields': ('organization', 'product_type', 'is_active'),
        }),
        ('Limites de simulacion', {
            'fields': ('min_amount', 'max_amount', 'min_term_months', 'max_term_months'),
        }),
        ('Condiciones financieras', {
            'fields': ('monthly_rate', 'commission_rate', 'commission_amount', 'vat_rate'),
        }),
        ('Reglas habilitadas', {
            'fields': ('allows_second_credit', 'allows_portfolio_takeover'),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorProfile)
class PerfilContratistaAdmin(AdminContratistasBase):
    list_display = ('user', 'organization', 'role', 'is_active', 'created_at')
    search_fields = ('user__username', 'user__email', 'organization__name', 'organization__subdomain')
    list_filter = ('is_active', 'role', 'organization')
    fieldsets = (
        ('Usuario y organizacion', {
            'fields': ('user', 'organization', 'role'),
        }),
        ('Estado', {
            'fields': ('is_active',),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorApplication)
class PreSolicitudContratistaAdmin(AdminContratistasBase):
    actions = ('accion_marcar_en_revision', 'accion_rechazar_solicitud', 'accion_evaluar_predecision')
    list_display = (
        'document_number',
        'nombre_solicitante',
        'usuario',
        'organization',
        'status',
        'escenario_credito',
        'requested_amount',
        'term_months',
        'created_at',
    )
    search_fields = (
        'document_number',
        'first_name',
        'last_name',
        'email',
        'phone',
        'usuario__username',
        'usuario__email',
        'organization__name',
        'organization__subdomain',
    )
    list_filter = ('status', 'escenario_credito', 'organization', 'accepted_terms', 'created_at')
    readonly_fields = (
        'created_at',
        'updated_at',
        'source_subdomain',
        'ip_address',
        'user_agent',
        'simulation_payload',
        'revisado_en',
        'revisado_por',
    )
    fieldsets = (
        ('Organizacion y estado', {
            'fields': ('organization', 'configuracion_portal', 'product_config', 'usuario', 'status', 'escenario_credito', 'credito'),
        }),
        ('Solicitud', {
            'fields': ('requested_amount', 'term_months', 'estimated_monthly_payment', 'accepted_terms'),
        }),
        ('Solicitante', {
            'fields': (
                'document_type',
                'document_number',
                'first_name',
                'last_name',
                'phone',
                'email',
                'address',
            ),
        }),
        ('Trazabilidad', {
            'fields': ('source_subdomain', 'ip_address', 'user_agent', 'simulation_payload'),
        }),
        ('Revision interna', {
            'fields': ('revisado_en', 'revisado_por', 'notas_revision'),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def get_actions(self, request):
        acciones = super().get_actions(request)
        if not request.user.has_perm('contractors.can_review_contractor_application'):
            acciones.pop('accion_marcar_en_revision', None)
            acciones.pop('accion_rechazar_solicitud', None)
        if not request.user.has_perm('contractors.can_evaluate_contractor_predecision'):
            acciones.pop('accion_evaluar_predecision', None)
        return acciones

    @admin.display(description='Solicitante')
    def nombre_solicitante(self, obj):
        return f'{obj.first_name} {obj.last_name}'.strip()

    @admin.action(description='Marcar seleccionadas en revision')
    def accion_marcar_en_revision(self, request, queryset):
        procesadas = 0
        for solicitud in queryset:
            try:
                marcar_solicitud_en_revision(
                    solicitud,
                    request.user,
                    observacion='Marcada en revision desde admin.',
                )
                procesadas += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
        self.message_user(request, f'{procesadas} pre-solicitudes marcadas en revision.')

    @admin.action(description='Rechazar seleccionadas')
    def accion_rechazar_solicitud(self, request, queryset):
        procesadas = 0
        for solicitud in queryset:
            try:
                rechazar_solicitud_contratista(
                    solicitud,
                    request.user,
                    motivo='Rechazada desde admin.',
                )
                procesadas += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
        self.message_user(request, f'{procesadas} pre-solicitudes rechazadas.')

    @admin.action(description='Evaluar predecision')
    def accion_evaluar_predecision(self, request, queryset):
        resumen = {
            'evaluadas': 0,
            'preaprobadas_read_only': 0,
            'revision_manual': 0,
            'bloqueadas': 0,
            'incompletas': 0,
            'errores': 0,
        }
        for solicitud in queryset:
            try:
                evaluacion = evaluar_formalmente_solicitud_prestador(
                    solicitud,
                    usuario=request.user,
                    request=request,
                )
                resumen['evaluadas'] += 1
                decision = evaluacion.resultado.decision
                if decision == 'PREAPROBADO_READ_ONLY':
                    resumen['preaprobadas_read_only'] += 1
                elif decision == 'REQUIERE_REVISION_MANUAL':
                    resumen['revision_manual'] += 1
                elif decision == 'BLOQUEADO_READ_ONLY':
                    resumen['bloqueadas'] += 1
                elif decision == 'INCOMPLETO':
                    resumen['incompletas'] += 1
            except Exception as exc:
                resumen['errores'] += 1
                self.message_user(
                    request,
                    f'No fue posible evaluar solicitud {solicitud.id}: {exc}',
                    level=messages.WARNING,
                )

        self.message_user(
            request,
            (
                'Evaluacion de predecision: '
                f"evaluadas={resumen['evaluadas']}, "
                f"preaprobadas_read_only={resumen['preaprobadas_read_only']}, "
                f"revision_manual={resumen['revision_manual']}, "
                f"bloqueadas={resumen['bloqueadas']}, "
                f"incompletas={resumen['incompletas']}, "
                f"errores={resumen['errores']}."
            ),
        )


@admin.register(SimulacionPrestador)
class SimulacionPrestadorAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'monto_solicitado',
        'plazo_meses',
        'cuota_mensual',
        'total_estimado',
        'aceptada',
        'accepted_at',
        'created_at',
    )
    search_fields = (
        'solicitud__id',
        'solicitud__document_number',
        'solicitud__email',
        'aceptada_por__username',
        'aceptada_por__email',
    )
    list_filter = ('aceptada', 'version_politica', 'created_at')
    readonly_fields = tuple(field.name for field in SimulacionPrestador._meta.fields)
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PredecisionPrestadorAudit)
class AuditoriaPredecisionPrestadorAdmin(admin.ModelAdmin):
    actions = None
    list_display = (
        'solicitud',
        'usuario',
        'escenario_credito',
        'decision',
        'eligible',
        'requiere_revision_manual',
        'score_banda',
        'datacredito_status',
        'datacredito_modo',
        'autorizacion_datacredito',
        'created_at',
    )
    search_fields = (
        'solicitud__id',
        'solicitud__document_number',
        'solicitud__email',
        'usuario__username',
        'usuario__email',
        'request_id',
    )
    list_filter = (
        'decision',
        'escenario_credito',
        'score_banda',
        'datacredito_status',
        'datacredito_modo',
        'created_at',
    )
    readonly_fields = (
        'solicitud',
        'usuario',
        'escenario_credito',
        'decision',
        'eligible',
        'requiere_revision_manual',
        'monto_maximo_sugerido',
        'plazo_maximo_sugerido',
        'score_status',
        'score_final',
        'score_banda',
        'score_version_configuracion',
        'datacredito_status',
        'datacredito_fuente',
        'datacredito_mora_severa',
        'datacredito_mora_actual',
        'autorizacion_datacredito',
        'snapshot_decisor',
        'snapshot_historial',
        'datacredito_modo',
        'decisor_reutilizado',
        'historial_reutilizado',
        'decisor_consultado',
        'historial_consultado',
        'justificacion_consulta_forzada',
        'capacidad_status',
        'riesgo_status',
        'bloqueos',
        'advertencias',
        'razones',
        'resultado_sanitizado',
        'request_id',
        'ip_address',
        'user_agent',
        'created_at',
    )
    fieldsets = (
        ('Solicitud', {
            'fields': ('solicitud', 'usuario', 'escenario_credito', 'decision', 'eligible', 'requiere_revision_manual'),
        }),
        ('Sugerencia read-only', {
            'fields': ('monto_maximo_sugerido', 'plazo_maximo_sugerido'),
        }),
        ('Score', {
            'fields': ('score_status', 'score_final', 'score_banda', 'score_version_configuracion'),
        }),
        ('DataCredito', {
            'fields': (
                'datacredito_status',
                'datacredito_fuente',
                'datacredito_mora_severa',
                'datacredito_mora_actual',
                'autorizacion_datacredito',
                'datacredito_modo',
                'snapshot_decisor',
                'snapshot_historial',
                'decisor_reutilizado',
                'historial_reutilizado',
                'decisor_consultado',
                'historial_consultado',
                'justificacion_consulta_forzada',
            ),
        }),
        ('Estados', {
            'fields': ('capacidad_status', 'riesgo_status'),
        }),
        ('Razones', {
            'fields': ('bloqueos', 'advertencias', 'razones'),
        }),
        ('Snapshot sanitizado', {
            'fields': ('resultado_sanitizado',),
        }),
        ('Trazabilidad', {
            'fields': ('request_id', 'ip_address', 'user_agent', 'created_at'),
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


@admin.register(AutorizacionConsultaDatacreditoPrestador)
class AutorizacionConsultaDatacreditoPrestadorAdmin(admin.ModelAdmin):
    actions = None
    list_display = (
        'solicitud',
        'usuario',
        'autorizado',
        'version_texto',
        'source',
        'estado_autorizacion',
        'accepted_at',
        'created_at',
    )
    search_fields = (
        'solicitud__id',
        'solicitud__document_number',
        'usuario__username',
        'usuario__email',
        'version_texto',
        'texto_hash',
    )
    list_filter = ('autorizado', 'version_texto', 'source', 'accepted_at', 'revoked_at')
    readonly_fields = tuple(field.name for field in AutorizacionConsultaDatacreditoPrestador._meta.fields)
    fieldsets = (
        ('Evidencia', {
            'fields': (
                'solicitud',
                'usuario',
                'autorizado',
                'version_texto',
                'texto_hash',
                'finalidad',
                'accepted_at',
                'source',
                'justificacion',
                'revoked_at',
            ),
        }),
        ('Trazabilidad tecnica', {
            'fields': ('ip_address', 'user_agent', 'created_at'),
        }),
    )

    @admin.display(description='Estado')
    def estado_autorizacion(self, obj):
        return 'Revocada' if obj.revoked_at else 'Vigente'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


@admin.register(NovedadPagadorPrestador)
class NovedadPagadorPrestadorAdmin(admin.ModelAdmin):
    actions = None
    list_display = ('credito', 'solicitud', 'empresa', 'tipo', 'estado', 'created_at', 'sent_at')
    search_fields = (
        'credito__numero_credito',
        'solicitud__document_number',
        'solicitud__first_name',
        'solicitud__last_name',
        'empresa__nombre',
        'created_by__email',
        'created_by__username',
    )
    list_filter = ('tipo', 'estado', 'empresa', 'created_at')
    readonly_fields = (
        'credito',
        'solicitud',
        'empresa',
        'tipo',
        'estado',
        'destinatarios',
        'metadata',
        'created_by',
        'request_id',
        'created_at',
        'sent_at',
    )
    fieldsets = (
        ('Novedad', {
            'fields': ('credito', 'solicitud', 'empresa', 'tipo', 'estado'),
        }),
        ('Destinatarios y metadata', {
            'fields': ('destinatarios', 'metadata'),
        }),
        ('Trazabilidad', {
            'fields': ('created_by', 'request_id', 'created_at', 'sent_at'),
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


@admin.register(TimelinePrestador)
class TimelinePrestadorAdmin(admin.ModelAdmin):
    actions = None
    list_display = (
        'created_at',
        'tipo_evento',
        'titulo',
        'solicitud',
        'credito',
        'estado_resultante',
        'usuario',
    )
    search_fields = (
        'titulo',
        'descripcion',
        'solicitud__document_number',
        'solicitud__first_name',
        'solicitud__last_name',
        'credito__numero_credito',
        'usuario__username',
        'usuario__email',
        'request_id',
    )
    list_filter = ('tipo_evento', 'estado_resultante', 'created_at')
    readonly_fields = (
        'solicitud',
        'credito',
        'tipo_evento',
        'titulo',
        'descripcion',
        'estado_resultante',
        'metadata',
        'usuario',
        'request_id',
        'ip_address',
        'user_agent',
        'created_at',
    )
    fieldsets = (
        ('Evento', {
            'fields': ('tipo_evento', 'titulo', 'descripcion', 'estado_resultante'),
        }),
        ('Referencias', {
            'fields': ('solicitud', 'credito', 'usuario'),
        }),
        ('Metadata segura', {
            'fields': ('metadata',),
        }),
        ('Trazabilidad', {
            'fields': ('request_id', 'ip_address', 'user_agent', 'created_at'),
        }),
    )
    ordering = ('-created_at', '-id')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


@admin.register(ContractorApplicationDocument)
class DocumentoSolicitudContratistaAdmin(AdminContratistasBase):
    actions = ('accion_aprobar_documento', 'accion_rechazar_documento')
    list_display = (
        'original_filename',
        'document_type',
        'estado_revision',
        'organizacion',
        'application',
        'uploaded_at',
    )
    search_fields = (
        'original_filename',
        'application__document_number',
        'application__first_name',
        'application__last_name',
        'application__organization__name',
        'application__organization__subdomain',
    )
    list_filter = (
        'document_type',
        'status',
        'application__organization',
        'uploaded_at',
    )
    readonly_fields = (
        'uploaded_at',
        'reviewed_at',
        'original_filename',
        'content_type',
        'file_size',
        'reviewed_by',
    )
    fieldsets = (
        ('Solicitud', {
            'fields': ('application',),
        }),
        ('Documento', {
            'fields': ('document_type', 'file', 'original_filename', 'content_type', 'file_size'),
        }),
        ('Revision', {
            'fields': ('status', 'reviewed_at', 'reviewed_by', 'review_notes'),
        }),
        ('Auditoria', {
            'fields': ('uploaded_at',),
        }),
    )

    @admin.display(description='Organizacion')
    def organizacion(self, obj):
        return obj.application.organization

    @admin.display(description='Estado')
    def estado_revision(self, obj):
        return obj.get_status_display()

    def get_actions(self, request):
        acciones = super().get_actions(request)
        if not request.user.has_perm('contractors.can_review_contractor_document'):
            acciones.pop('accion_aprobar_documento', None)
            acciones.pop('accion_rechazar_documento', None)
        return acciones

    @admin.action(description='Aprobar documentos seleccionados')
    def accion_aprobar_documento(self, request, queryset):
        procesados = 0
        for documento in queryset:
            try:
                aprobar_documento_solicitud(
                    documento,
                    request.user,
                    observacion='Aprobado desde admin.',
                )
                procesados += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
        self.message_user(request, f'{procesados} documentos aprobados.')

    @admin.action(description='Rechazar documentos seleccionados')
    def accion_rechazar_documento(self, request, queryset):
        procesados = 0
        for documento in queryset:
            try:
                rechazar_documento_solicitud(
                    documento,
                    request.user,
                    motivo='Rechazado desde admin.',
                )
                procesados += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
        self.message_user(request, f'{procesados} documentos rechazados.')


@admin.register(InformacionLaboralSolicitudContratista)
class InformacionLaboralSolicitudContratistaAdmin(AdminContratistasBase):
    list_display = (
        'solicitud',
        'cargo',
        'tipo_contrato',
        'empresa',
        'empresa_contratante_nombre',
        'fecha_inicio_contrato',
        'fecha_fin_contrato',
        'updated_at',
    )
    search_fields = (
        'solicitud__document_number',
        'solicitud__first_name',
        'solicitud__last_name',
        'cargo',
        'empresa__nombre',
        'empresa__nit',
        'empresa_contratante_nombre',
        'empresa_contratante_nit',
        'pagador_nombre',
        'pagador_email',
    )
    list_filter = (
        'tipo_contrato',
        'empresa',
        'empresa_contratante_nombre',
        'fecha_inicio_contrato',
        'fecha_fin_contrato',
        'created_at',
    )
    fieldsets = (
        ('Solicitud', {
            'fields': ('solicitud',),
        }),
        ('Contrato', {
            'fields': (
                'cargo',
                'tipo_contrato',
                'fecha_inicio_contrato',
                'fecha_fin_contrato',
                'valor_total_contrato',
                'valor_pagado_contrato',
                'valor_pendiente_cobrar',
            ),
        }),
        ('Empresa contratante y pagador', {
            'fields': (
                'empresa',
                'empresa_contratante_nombre',
                'empresa_contratante_nit',
                'pagador_nombre',
                'pagador_email',
                'pagador_telefono',
            ),
        }),
        ('Observaciones', {
            'fields': ('observaciones',),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )
