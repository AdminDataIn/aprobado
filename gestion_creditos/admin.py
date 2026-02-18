from django.contrib import admin, messages
from django import forms
from django.urls import path, reverse
from django.shortcuts import redirect, get_object_or_404
from django.utils.html import format_html
from django.template.response import TemplateResponse
from django.core.exceptions import ValidationError
from .models import (
    Credito, CreditoEmprendimiento, CreditoLibranza, Empresa, HistorialPago, WompiIntent,
    CuentaAhorro, MovimientoAhorro, ConfiguracionTasaInteres, ImagenNegocio, Notificacion,
    Pagare, ZapSignWebhookLog, MarketplaceItem, MarketplaceItemHistorialEstado
)
from django.utils import timezone
from datetime import timedelta
from .services.marketplace_service import (
    cambiar_estado_publicacion,
    es_transicion_estado_valida,
    registrar_historial_publicacion,
    notificar_empresa_estado_publicacion,
)

# Hotfix de etiquetas con tildes para el index de admin.
# Evita tocar masivamente cadenas en modelos por ahora.
Credito._meta.verbose_name = 'Crédito'
Credito._meta.verbose_name_plural = 'Créditos'
Pagare._meta.verbose_name = 'Pagaré'
Pagare._meta.verbose_name_plural = 'Pagarés'

#? --------- INLINE PARA IMÁGENES DEL NEGOCIO ------------
class ImagenNegocioInline(admin.TabularInline):
    model = ImagenNegocio
    extra = 0
    readonly_fields = ['fecha_subida', 'imagen_preview']
    fields = ['imagen', 'imagen_preview', 'tipo_imagen', 'descripcion', 'fecha_subida']

    def imagen_preview(self, obj):
        if obj.imagen:
            return f'<img src="{obj.imagen.url}" style="max-height: 100px; max-width: 100px;" />'
        return "Sin imagen"
    imagen_preview.allow_tags = True
    imagen_preview.short_description = "Vista previa"

#? --------- ADMINISTRACION DE CREDITOS ------------
class CreditoEmprendimientoInline(admin.StackedInline):
    model = CreditoEmprendimiento
    can_delete = False
    verbose_name_plural = 'Detalle de Emprendimiento'
    fk_name = 'credito'
    #! Hacemos los campos de solicitud readonly una vez creados (excepto numero_cedula y celular_wh para correcciones)
    readonly_fields = ('nombre', 'fecha_nac', 'direccion', 'estado_civil', 'numero_personas_cargo', 'nombre_negocio', 'ubicacion_negocio', 'tiempo_operando', 'dias_trabajados_sem', 'prod_serv_ofrec', 'ingresos_prom_mes', 'cli_aten_day', 'inventario', 'nomb_ref_per1', 'cel_ref_per1', 'rel_ref_per1', 'nomb_ref_cl1', 'cel_ref_cl1', 'rel_ref_cl1', 'ref_conoc_lid_com', 'foto_negocio', 'desc_fotos_neg', 'tipo_cta_mno', 'ahorro_tand_alc', 'depend_h', 'desc_cred_nec', 'redes_soc', 'fotos_prod', 'puntaje', 'puntaje_imagenes', 'datos_scoring_imagenes')
    # Agregar el inline de imágenes dentro del inline de emprendimiento
    inlines = [ImagenNegocioInline]

#? --------- ADMINISTRACION DE CREDITOS ------------
class CreditoLibranzaInline(admin.StackedInline):
    model = CreditoLibranza
    can_delete = False
    verbose_name_plural = 'Detalle de Libranza'
    fk_name = 'credito'

@admin.register(Credito)
class CreditoAdmin(admin.ModelAdmin):
    list_display = ('numero_credito', 'usuario', 'linea', 'estado', 'fecha_solicitud')
    list_filter = ('linea', 'estado', 'fecha_solicitud')
    search_fields = ('usuario__username', 'numero_credito')
    readonly_fields = ('numero_credito', 'fecha_solicitud', 'fecha_actualizacion', 'linea', 'usuario')
    inlines = [] #! Inlines se determinan dinámicamente

    def get_readonly_fields(self, request, obj=None):
        # Inicia con los campos de solo lectura definidos en la clase
        readonly_fields = list(super().get_readonly_fields(request, obj))
        # Si el objeto existe y su estado es ACTIVO, añade 'estado' a la lista
        if obj and obj.estado == Credito.EstadoCredito.ACTIVO:
            readonly_fields.append('estado')
        return readonly_fields

    def get_inlines(self, request, obj=None):
        if obj:
            if obj.linea == Credito.LineaCredito.EMPRENDIMIENTO:
                return [CreditoEmprendimientoInline]
            elif obj.linea == Credito.LineaCredito.LIBRANZA:
                return [CreditoLibranzaInline]
        return []

    def save_model(self, request, obj, form, change):
        """
        Lógica de negocio al aprobar un crédito de emprendimiento.
        """
        #! Guardar el objeto principal (Credito) primero
        super().save_model(request, obj, form, change)

        #! Comprobar si el estado se ha cambiado a APROBADO y si es un crédito de emprendimiento
        if ('estado' in form.changed_data and
                obj.estado == Credito.EstadoCredito.APROBADO and
                obj.linea == Credito.LineaCredito.EMPRENDIMIENTO):
            
            detalle = getattr(obj, 'detalle_emprendimiento', None)
            
            #? Asegurarse de que este proceso solo se ejecute una vez (cuando monto_aprobado no está seteado)
            if detalle and detalle.monto_aprobado is None:
                
                #? Lógica de negocio para calcular el valor de la cuota.
                if detalle.plazo > 0:
                    valor_cuota_calculado = detalle.valor_credito / detalle.plazo
                else:
                    valor_cuota_calculado = detalle.valor_credito

                #? Calcula la fecha del primer pago (ej: 30 días desde hoy)
                fecha_primer_pago = timezone.now().date() + timedelta(days=30)

                #? Actualizar el detalle del crédito de emprendimiento
                detalle.monto_aprobado = detalle.valor_credito
                detalle.saldo_pendiente = detalle.valor_credito
                detalle.valor_cuota = valor_cuota_calculado
                detalle.fecha_proximo_pago = fecha_primer_pago
                detalle.save()

@admin.register(CreditoEmprendimiento)
class CreditoEmprendimientoAdmin(admin.ModelAdmin):
    """
    Admin dedicado para corregir datos erróneos en CreditoEmprendimiento.
    Permite modificar campos como numero_cedula y celular_wh directamente.
    """
    list_display = ('credito_numero', 'nombre', 'numero_cedula', 'celular_wh', 'nombre_negocio', 'fecha_solicitud')
    list_filter = ('credito__estado', 'credito__fecha_solicitud')
    search_fields = ('nombre', 'numero_cedula', 'celular_wh', 'nombre_negocio', 'credito__numero_credito')
    readonly_fields = ('credito', 'fecha_nac', 'puntaje', 'puntaje_imagenes', 'datos_scoring_imagenes')

    fieldsets = (
        ('Información del Crédito', {
            'fields': ('credito',)
        }),
        ('Datos Personales (Editable para Correcciones)', {
            'fields': ('nombre', 'numero_cedula', 'celular_wh', 'fecha_nac', 'direccion', 'estado_civil', 'numero_personas_cargo'),
            'description': 'Estos campos pueden ser editados para corregir datos erróneos ingresados por error.'
        }),
        ('Información del Negocio', {
            'fields': ('nombre_negocio', 'ubicacion_negocio', 'tiempo_operando', 'dias_trabajados_sem',
                      'prod_serv_ofrec', 'ingresos_prom_mes', 'cli_aten_day', 'inventario'),
            'classes': ('collapse',)
        }),
        ('Referencias', {
            'fields': ('nomb_ref_per1', 'cel_ref_per1', 'rel_ref_per1',
                      'nomb_ref_cl1', 'cel_ref_cl1', 'rel_ref_cl1', 'ref_conoc_lid_com'),
            'classes': ('collapse',)
        }),
        ('Información Adicional', {
            'fields': ('tipo_cta_mno', 'ahorro_tand_alc', 'depend_h', 'desc_cred_nec',
                      'redes_soc', 'fotos_prod', 'desc_fotos_neg'),
            'classes': ('collapse',)
        }),
        ('Evaluación y Scoring', {
            'fields': ('puntaje', 'observaciones_analista', 'puntaje_imagenes', 'datos_scoring_imagenes'),
            'classes': ('collapse',)
        }),
    )

    def credito_numero(self, obj):
        return obj.credito.numero_credito
    credito_numero.short_description = 'Número de Crédito'
    credito_numero.admin_order_field = 'credito__numero_credito'

    def fecha_solicitud(self, obj):
        return obj.credito.fecha_solicitud
    fecha_solicitud.short_description = 'Fecha Solicitud'
    fecha_solicitud.admin_order_field = 'credito__fecha_solicitud'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('credito')

    actions = ['detectar_cedulas_invalidas']

    @admin.action(description='Detectar cédulas con datos no numéricos')
    def detectar_cedulas_invalidas(self, request, queryset):
        """
        Detecta y muestra los registros con cédulas que contienen datos no numéricos.
        """
        invalidos = []
        for obj in queryset:
            # Verificar si numero_cedula contiene caracteres no numéricos
            if not obj.numero_cedula.isdigit():
                invalidos.append(f"{obj.credito.numero_credito}: '{obj.nombre}' tiene cédula inválida '{obj.numero_cedula}'")

        if invalidos:
            self.message_user(
                request,
                f"Se encontraron {len(invalidos)} registros con cédulas inválidas:\n" + "\n".join(invalidos),
                level='warning'
            )
        else:
            self.message_user(request, "No se encontraron cédulas inválidas en la selección.", level='success')


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'slug', 'whatsapp_contacto')
    search_fields = ('nombre', 'slug')


class MarketplaceItemHistorialEstadoInline(admin.TabularInline):
    model = MarketplaceItemHistorialEstado
    extra = 0
    can_delete = False
    fields = ('fecha_cambio', 'estado_anterior', 'estado_nuevo', 'origen', 'usuario', 'comentario')
    readonly_fields = ('fecha_cambio', 'estado_anterior', 'estado_nuevo', 'origen', 'usuario', 'comentario')

    def has_add_permission(self, request, obj=None):
        return False


class RechazoMarketplaceForm(forms.Form):
    motivo = forms.CharField(
        label='Motivo del rechazo',
        widget=forms.Textarea(attrs={'rows': 4, 'style': 'width:100%;'}),
        required=True
    )


class MarketplaceItemAdminForm(forms.ModelForm):
    class Meta:
        model = MarketplaceItem
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        if self.instance and self.instance.pk:
            estado_actual = MarketplaceItem.objects.get(pk=self.instance.pk).estado
            estado_nuevo = cleaned_data.get('estado')
            if estado_nuevo == MarketplaceItem.EstadoItem.RECHAZADO and estado_actual != MarketplaceItem.EstadoItem.RECHAZADO:
                raise forms.ValidationError(
                    'Para rechazar una publicacion usa el boton "Rechazar" de la lista (exige motivo).'
                )
            if estado_nuevo and not es_transicion_estado_valida(estado_actual, estado_nuevo):
                raise forms.ValidationError(
                    f'Transicion invalida: {estado_actual} -> {estado_nuevo}.'
                )
        return cleaned_data


@admin.register(MarketplaceItem)
class MarketplaceItemAdmin(admin.ModelAdmin):
    form = MarketplaceItemAdminForm
    list_display = ('titulo', 'empresa', 'tipo', 'estado', 'fecha_creacion', 'acciones_estado')
    list_filter = ('tipo', 'estado', 'empresa')
    search_fields = ('titulo', 'empresa__nombre')
    readonly_fields = ('fecha_creacion', 'fecha_publicacion')
    inlines = [MarketplaceItemHistorialEstadoInline]

    def save_model(self, request, obj, form, change):
        estado_anterior = None
        if change and obj.pk:
            estado_anterior = MarketplaceItem.objects.get(pk=obj.pk).estado

        super().save_model(request, obj, form, change)

        if not change:
            registrar_historial_publicacion(
                item=obj,
                estado_anterior='',
                estado_nuevo=obj.estado,
                origen=MarketplaceItemHistorialEstado.OrigenCambio.ADMIN,
                usuario=request.user,
                comentario='Publicacion creada desde admin.'
            )
            return

        if estado_anterior != obj.estado:
            if obj.estado == MarketplaceItem.EstadoItem.APROBADO and not obj.fecha_publicacion:
                obj.fecha_publicacion = timezone.now()
                obj.save(update_fields=['fecha_publicacion'])

            registrar_historial_publicacion(
                item=obj,
                estado_anterior=estado_anterior or '',
                estado_nuevo=obj.estado,
                origen=MarketplaceItemHistorialEstado.OrigenCambio.ADMIN,
                usuario=request.user,
                comentario='Cambio de estado realizado desde admin.'
            )
            notificar_empresa_estado_publicacion(
                item=obj,
                estado_nuevo=obj.estado,
                comentario='Cambio de estado realizado desde admin.'
            )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:item_id>/aprobar/',
                self.admin_site.admin_view(self.aprobar_publicacion),
                name='gestion_creditos_marketplaceitem_aprobar'
            ),
            path(
                '<int:item_id>/rechazar/',
                self.admin_site.admin_view(self.rechazar_publicacion),
                name='gestion_creditos_marketplaceitem_rechazar'
            ),
        ]
        return custom_urls + urls

    def acciones_estado(self, obj):
        if obj.estado == MarketplaceItem.EstadoItem.PENDIENTE:
            aprobar_url = reverse('admin:gestion_creditos_marketplaceitem_aprobar', args=[obj.pk])
            rechazar_url = reverse('admin:gestion_creditos_marketplaceitem_rechazar', args=[obj.pk])
            return format_html(
                '<a class="button" href="{}">Aprobar</a>&nbsp;<a class="button" href="{}">Rechazar</a>',
                aprobar_url,
                rechazar_url
            )
        return '-'
    acciones_estado.short_description = 'Aprobacion'

    def aprobar_publicacion(self, request, item_id):
        item = get_object_or_404(MarketplaceItem, pk=item_id)
        try:
            cambiar_estado_publicacion(
                item=item,
                estado_nuevo=MarketplaceItem.EstadoItem.APROBADO,
                usuario=request.user,
                origen=MarketplaceItemHistorialEstado.OrigenCambio.ADMIN,
                comentario='Publicacion aprobada por administrador.'
            )
            self.message_user(request, f'Publicacion "{item.titulo}" aprobada.')
        except ValidationError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        return redirect(request.META.get('HTTP_REFERER', reverse('admin:gestion_creditos_marketplaceitem_changelist')))

    def rechazar_publicacion(self, request, item_id):
        item = get_object_or_404(MarketplaceItem, pk=item_id)
        if request.method == 'POST':
            form = RechazoMarketplaceForm(request.POST)
            if form.is_valid():
                motivo = form.cleaned_data['motivo']
                try:
                    cambiar_estado_publicacion(
                        item=item,
                        estado_nuevo=MarketplaceItem.EstadoItem.RECHAZADO,
                        usuario=request.user,
                        origen=MarketplaceItemHistorialEstado.OrigenCambio.ADMIN,
                        comentario=motivo,
                        require_comment=True
                    )
                    self.message_user(request, f'Publicacion "{item.titulo}" rechazada.')
                    return redirect(reverse('admin:gestion_creditos_marketplaceitem_changelist'))
                except ValidationError as exc:
                    self.message_user(request, str(exc), level=messages.ERROR)
            self.message_user(request, 'Debe ingresar un motivo para rechazar.', level=messages.ERROR)
        else:
            form = RechazoMarketplaceForm()

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': f"Rechazar publicacion: {item.titulo}",
            'item': item,
            'form': form,
        }
        return TemplateResponse(request, 'admin/gestion_creditos/marketplaceitem/rechazar_publicacion.html', context)


@admin.register(MarketplaceItemHistorialEstado)
class MarketplaceItemHistorialEstadoAdmin(admin.ModelAdmin):
    list_display = ('item', 'estado_anterior', 'estado_nuevo', 'origen', 'usuario', 'fecha_cambio')
    list_filter = ('origen', 'estado_nuevo', 'fecha_cambio')
    search_fields = ('item__titulo', 'item__empresa__nombre', 'usuario__username', 'comentario')
    readonly_fields = ('item', 'estado_anterior', 'estado_nuevo', 'origen', 'usuario', 'comentario', 'fecha_cambio')

@admin.register(HistorialPago)
class HistorialPagoAdmin(admin.ModelAdmin):
    list_display = ('credito', 'fecha_pago', 'monto', 'estado', 'referencia_pago')
    list_filter = ('estado', 'fecha_pago')
    search_fields = ('credito__numero_credito', 'referencia_pago')

@admin.register(WompiIntent)
class WompiIntentAdmin(admin.ModelAdmin):
    list_display = ('credito', 'referencia', 'amount_in_cents', 'status', 'wompi_transaction_id', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('credito__numero_credito', 'referencia', 'wompi_transaction_id')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(CuentaAhorro)
class CuentaAhorroAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'tipo_usuario', 'saldo_disponible', 'saldo_objetivo', 'activa', 'fecha_apertura')
    list_filter = ('tipo_usuario', 'activa', 'fecha_apertura')
    search_fields = ('usuario__username', 'usuario__email', 'usuario__first_name', 'usuario__last_name')
    readonly_fields = ('fecha_apertura', 'fecha_actualizacion')

@admin.register(MovimientoAhorro)
class MovimientoAhorroAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'cuenta', 'tipo', 'monto', 'estado', 'fecha_creacion', 'procesado_por')
    list_filter = ('tipo', 'estado', 'fecha_creacion')
    search_fields = ('referencia', 'cuenta__usuario__username', 'descripcion')
    readonly_fields = ('referencia', 'fecha_creacion', 'fecha_procesamiento')
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cuenta__usuario', 'procesado_por')

@admin.register(ConfiguracionTasaInteres)
class ConfiguracionTasaInteresAdmin(admin.ModelAdmin):
    list_display = ('tasa_anual_efectiva', 'fecha_vigencia', 'activa')
    list_filter = ('activa', 'fecha_vigencia')


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'usuario', 'tipo', 'leida', 'fecha_creacion')
    list_filter = ('tipo', 'leida', 'fecha_creacion')
    search_fields = ('usuario__username', 'usuario__email', 'titulo', 'mensaje')
    readonly_fields = ('fecha_creacion', 'fecha_leida')
    list_per_page = 25

    fieldsets = (
        ('Información General', {
            'fields': ('usuario', 'tipo', 'titulo', 'mensaje')
        }),
        ('Estado', {
            'fields': ('leida', 'fecha_creacion', 'fecha_leida')
        }),
        ('Acción', {
            'fields': ('url',),
            'description': 'URL a la que redirige cuando el usuario hace clic en la notificación'
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('usuario')


#? ----- ADMINISTRACIÓN DE PAGARÉS (ZapSign) -----
@admin.register(Pagare)
class PagareAdmin(admin.ModelAdmin):
    list_display = ('numero_pagare', 'credito', 'estado', 'fecha_creacion', 'fecha_firma', 'creado_por')
    list_filter = ('estado', 'fecha_creacion', 'fecha_firma')
    search_fields = ('numero_pagare', 'credito__numero_credito', 'zapsign_doc_token')
    readonly_fields = (
        'numero_pagare', 'fecha_creacion', 'fecha_envio', 'fecha_firma', 'fecha_rechazo',
        'zapsign_doc_token', 'zapsign_sign_url', 'zapsign_signed_file_url', 'hash_pdf',
        'ip_firmante', 'evidencias', 'creado_por'
    )

    fieldsets = (
        ('Información del Pagaré', {
            'fields': ('numero_pagare', 'credito', 'estado', 'version_plantilla')
        }),
        ('Archivos PDF', {
            'fields': ('archivo_pdf', 'archivo_pdf_firmado', 'hash_pdf')
        }),
        ('Integración ZapSign', {
            'fields': ('zapsign_doc_token', 'zapsign_sign_url', 'zapsign_signed_file_url', 'zapsign_status'),
            'classes': ('collapse',)
        }),
        ('Fechas y Auditoría', {
            'fields': ('fecha_creacion', 'fecha_envio', 'fecha_firma', 'fecha_rechazo', 'creado_por')
        }),
        ('Evidencia Forense', {
            'fields': ('ip_firmante', 'evidencias'),
            'classes': ('collapse',),
            'description': 'Datos de trazabilidad legal para disputas'
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('credito', 'creado_por')


@admin.register(ZapSignWebhookLog)
class ZapSignWebhookLogAdmin(admin.ModelAdmin):
    list_display = ('received_at', 'event', 'doc_token', 'signature_valid', 'processed', 'ip_address')
    list_filter = ('event', 'signature_valid', 'processed', 'received_at')
    search_fields = ('doc_token', 'event', 'ip_address')
    readonly_fields = ('doc_token', 'event', 'payload', 'headers', 'signature_valid', 'processed', 'error_message', 'received_at', 'ip_address')
    list_per_page = 50

    fieldsets = (
        ('Información del Evento', {
            'fields': ('doc_token', 'event', 'received_at', 'ip_address')
        }),
        ('Validación', {
            'fields': ('signature_valid', 'processed', 'error_message')
        }),
        ('Payload Completo', {
            'fields': ('payload', 'headers'),
            'classes': ('collapse',),
            'description': 'Datos completos del webhook para auditoría'
        }),
    )

    def has_add_permission(self, request):
        # No permitir crear webhooks manualmente
        return False

    def has_change_permission(self, request, obj=None):
        # Solo lectura
        return False
