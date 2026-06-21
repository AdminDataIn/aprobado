from django.contrib import admin

from integrations.models import ConsultaDatacreditoSnapshot


@admin.register(ConsultaDatacreditoSnapshot)
class ConsultaDatacreditoSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'servicio',
        'ambiente',
        'documento_enmascarado',
        'estado_normalizado',
        'con_informacion',
        'utilizable_para_score',
        'requiere_revision_manual',
        'consulted_at',
        'vigente_hasta',
        'source',
        'autorizacion_version_texto',
    )
    list_filter = (
        'servicio',
        'ambiente',
        'estado_normalizado',
        'con_informacion',
        'utilizable_para_score',
        'requiere_revision_manual',
        'source',
    )
    search_fields = ('documento_enmascarado', 'documento_hash', 'request_fingerprint', 'codigo_funcional')
    readonly_fields = (
        'id',
        'servicio',
        'ambiente',
        'proveedor',
        'tipo_documento',
        'request_fingerprint',
        'documento_hash',
        'documento_enmascarado',
        'estado_normalizado',
        'http_status',
        'codigo_funcional',
        'proveedor_respondio',
        'consulta_procesada',
        'con_informacion',
        'utilizable_para_score',
        'requiere_revision_manual',
        'requiere_revision_cumplimiento',
        'resultado_normalizado',
        'consulted_at',
        'vigente_hasta',
        'created_by',
        'request_id',
        'source',
        'autorizacion_id',
        'autorizacion_version_texto',
        'autorizacion_texto_hash',
        'autorizacion_accepted_at',
        'created_at',
    )
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}
