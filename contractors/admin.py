from django.contrib import admin

from contractors.models import (
    AutorizacionConsultaDatacreditoPrestador,
    ConfiguracionSimuladorPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    PredecisionPrestadorAudit,
    TimelinePrestador,
)


class ContractorApplicationDocumentInline(admin.TabularInline):
    model = ContractorApplicationDocument
    extra = 0
    readonly_fields = ['uploaded_by', 'created_at', 'updated_at']


@admin.register(ContractorApplication)
class ContractorApplicationAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'nombre_completo',
        'numero_documento',
        'empresa',
        'usuario',
        'estado',
        'created_at',
    ]
    list_filter = ['estado', 'empresa', 'escenario_credito', 'created_at']
    search_fields = [
        'nombres',
        'apellidos',
        'numero_documento',
        'correo',
        'usuario__email',
        'usuario__username',
        'empresa__nombre',
    ]
    readonly_fields = ['created_at', 'updated_at']
    inlines = [ContractorApplicationDocumentInline]


@admin.register(ContractorApplicationDocument)
class ContractorApplicationDocumentAdmin(admin.ModelAdmin):
    list_display = ['id', 'solicitud', 'tipo_documento', 'uploaded_by', 'created_at']
    list_filter = ['tipo_documento', 'created_at']
    search_fields = ['solicitud__numero_documento', 'solicitud__nombres', 'solicitud__apellidos']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ConfiguracionSimuladorPrestador)
class ConfiguracionSimuladorPrestadorAdmin(admin.ModelAdmin):
    list_display = [
        'nombre',
        'activo',
        'monto_minimo',
        'monto_maximo',
        'plazo_minimo_meses',
        'plazo_maximo_meses',
        'tasa_mensual',
        'updated_at',
    ]
    list_filter = ['activo']
    readonly_fields = ['created_at', 'updated_at']


class AdminAuditoriaSoloLectura(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PredecisionPrestadorAudit)
class PredecisionPrestadorAuditAdmin(AdminAuditoriaSoloLectura):
    list_display = [
        'id',
        'solicitud',
        'resultado',
        'estado_ejecucion',
        'score',
        'version_politica',
        'iniciada_en',
        'finalizada_en',
    ]
    list_filter = ['resultado', 'estado_ejecucion', 'version_politica', 'created_at']
    search_fields = ['solicitud__id', 'clave_idempotencia', 'version_datos']
    readonly_fields = [campo.name for campo in PredecisionPrestadorAudit._meta.fields]
    ordering = ['-created_at', '-id']


@admin.register(TimelinePrestador)
class TimelinePrestadorAdmin(AdminAuditoriaSoloLectura):
    list_display = [
        'id',
        'solicitud',
        'tipo_evento',
        'titulo',
        'visible_cliente',
        'creado_por',
        'created_at',
    ]
    list_filter = ['tipo_evento', 'visible_cliente', 'created_at']
    search_fields = ['solicitud__id', 'titulo']
    readonly_fields = [campo.name for campo in TimelinePrestador._meta.fields]
    ordering = ['-created_at', '-id']


@admin.register(AutorizacionConsultaDatacreditoPrestador)
class AutorizacionConsultaDatacreditoPrestadorAdmin(AdminAuditoriaSoloLectura):
    list_display = [
        'id',
        'solicitud',
        'usuario',
        'version_texto',
        'autorizada',
        'aceptada_en',
    ]
    list_filter = ['autorizada', 'version_texto', 'aceptada_en']
    search_fields = ['solicitud__id', 'usuario__email', 'texto_hash']
    readonly_fields = [
        campo.name for campo in AutorizacionConsultaDatacreditoPrestador._meta.fields
    ]
    ordering = ['-aceptada_en', '-id']
