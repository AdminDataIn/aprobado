from django.contrib import admin

from integrations.models import ConsultaDatacreditoSnapshot


@admin.register(ConsultaDatacreditoSnapshot)
class ConsultaDatacreditoSnapshotAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'servicio',
        'ambiente',
        'estado',
        'documento_enmascarado',
        'consultado_en',
        'vigente_hasta',
        'reutilizable',
    ]
    list_filter = ['ambiente', 'servicio', 'estado', 'consultado_en']
    search_fields = ['id', 'fingerprint', 'documento_hash']
    readonly_fields = [campo.name for campo in ConsultaDatacreditoSnapshot._meta.fields]
    ordering = ['-consultado_en', '-created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
