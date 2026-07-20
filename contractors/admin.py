from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied, ValidationError
from django.forms.models import BaseInlineFormSet
from django.template.response import TemplateResponse

from contractors.models import (
    AprobacionInternaPrestador,
    AutorizacionConsultaDatacreditoPrestador,
    BandaScorePrestador,
    ConfiguracionSimuladorPrestador,
    ConfiguracionScorePrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    FormalizacionCreditoPrestador,
    NovedadOperativaPrestador,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
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
    actions = ['ejecutar_evaluacion_formal']

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm('contractors.can_evaluate_contractor_application'):
            actions.pop('ejecutar_evaluacion_formal', None)
        return actions

    @admin.action(description='Ejecutar evaluacion formal read-only')
    def ejecutar_evaluacion_formal(self, request, queryset):
        from contractors.services.evaluacion_formal import evaluar_solicitud_prestador

        if not request.user.has_perm('contractors.can_evaluate_contractor_application'):
            self.message_user(request, 'No tienes permiso para evaluar solicitudes.', messages.ERROR)
            return
        if request.POST.get('confirmar_evaluacion') != '1':
            contexto = {
                **self.admin_site.each_context(request),
                'title': 'Confirmar evaluacion formal read-only',
                'opts': self.model._meta,
                'solicitudes': queryset,
                'action_checkbox_name': ACTION_CHECKBOX_NAME,
            }
            return TemplateResponse(
                request,
                'admin/contractors/confirmar_evaluacion_formal.html',
                contexto,
            )
        evaluadas = reutilizadas = errores = 0
        for solicitud in queryset.order_by('id'):
            try:
                resultado = evaluar_solicitud_prestador(
                    solicitud,
                    solicitado_por=request.user,
                )
                evaluadas += 1
                reutilizadas += int(resultado.reutilizada)
            except (ValidationError, PermissionDenied) as exc:
                errores += 1
                self.message_user(
                    request,
                    f'Solicitud {solicitud.pk}: {exc}',
                    messages.WARNING,
                )
        self.message_user(
            request,
            f'Evaluadas: {evaluadas}. Reutilizadas: {reutilizadas}. Errores: {errores}.',
            messages.SUCCESS if not errores else messages.WARNING,
        )


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
        'version',
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


class BandaScorePrestadorInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        bandas = []
        for form in self.forms:
            datos = form.cleaned_data
            if not datos or datos.get('DELETE'):
                continue
            minimo = datos.get('score_min')
            maximo = datos.get('score_max')
            monto = datos.get('monto_maximo')
            plazo = datos.get('plazo_maximo')
            if None in {minimo, monto, plazo}:
                continue
            limite = 1000 if maximo is None else maximo
            if minimo > limite:
                raise ValidationError('El score minimo no puede superar el maximo.')
            if plazo > self.instance.plazo_maximo_politica:
                raise ValidationError('Una banda supera el plazo maximo de la politica.')
            if monto > self.instance.monto_maximo_politica:
                raise ValidationError('Una banda supera el monto maximo de la politica.')
            bandas.append((minimo, limite, datos.get('nombre')))

        bandas.sort(key=lambda item: item[0])
        for anterior, actual in zip(bandas, bandas[1:]):
            if actual[0] <= anterior[1]:
                raise ValidationError('Las bandas de score no pueden solaparse.')
        if self.instance.activa:
            if len(bandas) != len(BandaScorePrestador.Nombre.values):
                raise ValidationError('Una politica activa debe tener exactamente cinco bandas.')
            if bandas[0][0] != 0 or bandas[-1][1] != 1000:
                raise ValidationError('Las bandas activas deben cubrir el rango completo 0 a 1000.')
            if any(actual[0] != anterior[1] + 1 for anterior, actual in zip(bandas, bandas[1:])):
                raise ValidationError('Las bandas activas no pueden dejar vacios de score.')


class BandaScorePrestadorInline(admin.TabularInline):
    model = BandaScorePrestador
    formset = BandaScorePrestadorInlineFormSet
    extra = 0


@admin.register(ConfiguracionScorePrestador)
class ConfiguracionScorePrestadorAdmin(admin.ModelAdmin):
    list_display = [
        'nombre', 'version', 'activa', 'fecha_vigencia_desde',
        'fecha_vigencia_hasta', 'configuracion_financiera',
        'version_score', 'version_politica',
    ]
    list_filter = ['activa', 'fecha_vigencia_desde', 'fecha_vigencia_hasta']
    search_fields = ['nombre', 'version', 'version_score', 'version_politica']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [BandaScorePrestadorInline]
    fieldsets = [
        ('Version y vigencia', {'fields': (
            'nombre', 'version', 'activa', 'fecha_vigencia_desde',
            'fecha_vigencia_hasta', 'configuracion_financiera',
            'version_score', 'version_politica',
        )}),
        ('Pesos (deben sumar 1)', {'fields': (
            'peso_datacredito', 'peso_capacidad', 'peso_comportamiento',
            'peso_riesgo', 'peso_referencias',
        )}),
        ('Politica', {'fields': (
            'score_premium_min', 'score_alta_min', 'score_media_min',
            'score_entrada_min', 'cuota_ingreso_maxima',
            'monto_maximo_politica', 'plazo_maximo_politica',
            'tasa_mensual_referencia', 'mora_bloqueo_dias',
            'consultas_recientes_revision', 'accion_exceso_capacidad',
        )}),
        ('Componentes opcionales', {'fields': (
            'requiere_referencias', 'permite_redistribuir_pesos_faltantes',
            'penalizacion_geolocalizacion', 'umbral_geolocalizacion',
        )}),
        ('Auditoria', {'fields': ('created_at', 'updated_at')}),
    ]


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
        'banda_score',
        'version_politica',
        'version_configuracion_financiera',
        'iniciada_en',
        'finalizada_en',
    ]
    list_filter = ['resultado', 'estado_ejecucion', 'version_politica', 'created_at']
    search_fields = ['solicitud__id', 'clave_idempotencia', 'version_datos']
    readonly_fields = [campo.name for campo in PredecisionPrestadorAudit._meta.fields]
    ordering = ['-created_at', '-id']

    @admin.display(description='Banda')
    def banda_score(self, obj):
        score = (obj.snapshot_salida or {}).get('score_resultado') or {}
        return score.get('banda') or '-'


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


@admin.register(RevisionManualPrestador)
class RevisionManualPrestadorAdmin(AdminAuditoriaSoloLectura):
    list_display = [
        'id', 'solicitud', 'estado', 'motivo', 'prioridad', 'asignado_a', 'creada_en',
    ]
    list_filter = ['estado', 'motivo', 'prioridad', 'creada_en']
    search_fields = ['solicitud__id', 'solicitud__nombres', 'solicitud__apellidos']
    readonly_fields = [campo.name for campo in RevisionManualPrestador._meta.fields]
    ordering = ['-creada_en', '-id']


@admin.register(RequerimientoSubsanacionPrestador)
class RequerimientoSubsanacionPrestadorAdmin(AdminAuditoriaSoloLectura):
    list_display = ['id', 'solicitud', 'revision', 'tipo', 'estado', 'creado_en']
    list_filter = ['tipo', 'estado', 'creado_en']
    search_fields = ['solicitud__id']
    readonly_fields = [campo.name for campo in RequerimientoSubsanacionPrestador._meta.fields]
    ordering = ['-creado_en', '-id']


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


@admin.register(AprobacionInternaPrestador)
class AprobacionInternaPrestadorAdmin(AdminAuditoriaSoloLectura):
    list_display = [
        'id', 'solicitud', 'estado', 'decision', 'monto_autorizado',
        'plazo_autorizado', 'decidida_por', 'creada_en', 'decidida_en',
    ]
    list_filter = ['estado', 'decision', 'motivo', 'version_politica', 'creada_en']
    search_fields = ['solicitud__id', 'auditoria_predecision__id']
    readonly_fields = [campo.name for campo in AprobacionInternaPrestador._meta.fields]
    ordering = ['-creada_en', '-id']


@admin.register(FormalizacionCreditoPrestador)
class FormalizacionCreditoPrestadorAdmin(AdminAuditoriaSoloLectura):
    list_display = [
        'id', 'credito', 'estado', 'estado_identidad', 'proveedor_firma',
        'intentos_firma', 'enviada_firma_en', 'firmada_en', 'created_at',
    ]
    list_filter = [
        'estado', 'estado_identidad', 'proveedor_firma', 'created_at',
    ]
    search_fields = [
        'credito__numero_credito', 'clave_idempotencia',
        'origen_credito_prestador__gate_id',
    ]
    readonly_fields = [
        campo.name for campo in FormalizacionCreditoPrestador._meta.fields
    ]
    ordering = ['-created_at', '-id']


@admin.register(NovedadOperativaPrestador)
class NovedadOperativaPrestadorAdmin(AdminAuditoriaSoloLectura):
    list_display = [
        'id', 'credito', 'empresa', 'estado', 'canal_envio', 'intentos_envio',
        'generada_en', 'enviada_en', 'recibida_en', 'gestionada_en',
    ]
    list_filter = ['estado', 'canal_envio', 'tipo_novedad', 'empresa', 'created_at']
    search_fields = ['credito__numero_credito', 'clave_idempotencia', 'empresa__nombre']
    readonly_fields = [campo.name for campo in NovedadOperativaPrestador._meta.fields]
    ordering = ['-created_at', '-id']
