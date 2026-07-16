from django.contrib import admin

from contractors.models import (
    ConfiguracionSimuladorPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
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
