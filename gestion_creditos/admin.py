from django.contrib import admin, messages
from django import forms
from django.conf import settings
from django.urls import path, reverse
from django.shortcuts import redirect, get_object_or_404
from django.utils.html import format_html
from django.template.response import TemplateResponse
from django.core.exceptions import ValidationError
from .models import (
    AsesorComercial, Credito, CreditoEmprendimiento, CreditoLibranza, Empresa, HistorialPago, WompiIntent, LotePagoEmpresa,
    CuentaAhorro, MovimientoAhorro, ConfiguracionTasaInteres, ImagenNegocio, Notificacion,
    Pagare, ZapSignWebhookLog, MarketplaceItem, MarketplaceItemHistorialEstado,
    MarketplacePedido, MarketplacePedidoItem, MarketplacePago, MarketplaceDireccionEntrega,
    MarketplaceLiquidacionEmpresa, InvestorAccount, InvestmentPosition, InvestmentCashflow,
    InvestmentReturnSnapshot, InvestmentEvent, VinculoLaboralEmpresa, CreditoAdelantoNomina
)
from django.utils import timezone
from datetime import timedelta
from gestion_creditos.services.advisors import get_asesor_performance_snapshot
from usuarios.executive_activation_service import enviar_invitacion_activacion_ejecutivo
from .services.marketplace_service import (
    cambiar_estado_publicacion,
    es_transicion_estado_valida,
    registrar_historial_publicacion,
    notificar_empresa_estado_publicacion,
)
from usuarios.investor_activation_service import enviar_invitacion_inversionista

# Hotfix de etiquetas con tildes para el index de admin.
# Evita tocar masivamente cadenas en modelos por ahora.
Credito._meta.verbose_name = 'CrÃ©dito'
Credito._meta.verbose_name_plural = 'CrÃ©ditos'
Pagare._meta.verbose_name = 'PagarÃ©'
Pagare._meta.verbose_name_plural = 'PagarÃ©s'

#? --------- INLINE PARA IMÃGENES DEL NEGOCIO ------------
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
    # Agregar el inline de imÃ¡genes dentro del inline de emprendimiento
    inlines = [ImagenNegocioInline]

#? --------- ADMINISTRACION DE CREDITOS ------------
class CreditoLibranzaInline(admin.StackedInline):
    model = CreditoLibranza
    can_delete = False
    verbose_name_plural = 'Detalle de Libranza'
    fk_name = 'credito'


class CreditoAdelantoNominaInline(admin.StackedInline):
    model = CreditoAdelantoNomina
    can_delete = False
    verbose_name_plural = 'Detalle de Adelanto de Nomina'
    fk_name = 'credito'

@admin.register(Credito)
class CreditoAdmin(admin.ModelAdmin):
    list_display = ('numero_credito', 'usuario', 'linea', 'estado', 'fecha_solicitud')
    list_filter = ('linea', 'estado', 'fecha_solicitud')
    search_fields = ('usuario__username', 'numero_credito')
    readonly_fields = ('numero_credito', 'fecha_solicitud', 'fecha_actualizacion', 'linea', 'usuario')
    inlines = [] #! Inlines se determinan dinÃ¡micamente

    def get_readonly_fields(self, request, obj=None):
        # Inicia con los campos de solo lectura definidos en la clase
        readonly_fields = list(super().get_readonly_fields(request, obj))
        # Si el objeto existe y su estado es ACTIVO, aÃ±ade 'estado' a la lista
        if obj and obj.estado == Credito.EstadoCredito.ACTIVO:
            readonly_fields.append('estado')
        return readonly_fields

    def get_inlines(self, request, obj=None):
        if obj:
            if obj.linea == Credito.LineaCredito.EMPRENDIMIENTO:
                return [CreditoEmprendimientoInline]
            elif obj.linea == Credito.LineaCredito.LIBRANZA:
                return [CreditoLibranzaInline]
            elif obj.linea == Credito.LineaCredito.ADELANTO_NOMINA:
                return [CreditoAdelantoNominaInline]
        return []

    def save_model(self, request, obj, form, change):
        """
        LÃ³gica de negocio al aprobar un crÃ©dito de emprendimiento.
        """
        #! Guardar el objeto principal (Credito) primero
        super().save_model(request, obj, form, change)

        #! Comprobar si el estado se ha cambiado a APROBADO y si es un crÃ©dito de emprendimiento
        if ('estado' in form.changed_data and
                obj.estado == Credito.EstadoCredito.APROBADO and
                obj.linea == Credito.LineaCredito.EMPRENDIMIENTO):
            
            detalle = getattr(obj, 'detalle_emprendimiento', None)
            
            #? Asegurarse de que este proceso solo se ejecute una vez (cuando monto_aprobado no estÃ¡ seteado)
            if detalle and detalle.monto_aprobado is None:
                
                #? LÃ³gica de negocio para calcular el valor de la cuota.
                if detalle.plazo > 0:
                    valor_cuota_calculado = detalle.valor_credito / detalle.plazo
                else:
                    valor_cuota_calculado = detalle.valor_credito

                #? Calcula la fecha del primer pago (ej: 30 dÃ­as desde hoy)
                fecha_primer_pago = timezone.now().date() + timedelta(days=30)

                #? Actualizar el detalle del crÃ©dito de emprendimiento
                detalle.monto_aprobado = detalle.valor_credito
                detalle.saldo_pendiente = detalle.valor_credito
                detalle.valor_cuota = valor_cuota_calculado
                detalle.fecha_proximo_pago = fecha_primer_pago
                detalle.save()

@admin.register(CreditoEmprendimiento)
class CreditoEmprendimientoAdmin(admin.ModelAdmin):
    """
    Admin dedicado para corregir datos errÃ³neos en CreditoEmprendimiento.
    Permite modificar campos como numero_cedula y celular_wh directamente.
    """
    list_display = ('credito_numero', 'nombre', 'numero_cedula', 'celular_wh', 'nombre_negocio', 'fecha_solicitud')
    list_filter = ('credito__estado', 'credito__fecha_solicitud')
    search_fields = ('nombre', 'numero_cedula', 'celular_wh', 'nombre_negocio', 'credito__numero_credito')
    readonly_fields = ('credito', 'fecha_nac', 'puntaje', 'puntaje_imagenes', 'datos_scoring_imagenes')

    fieldsets = (
        ('InformaciÃ³n del CrÃ©dito', {
            'fields': ('credito',)
        }),
        ('Datos Personales (Editable para Correcciones)', {
            'fields': ('nombre', 'numero_cedula', 'celular_wh', 'fecha_nac', 'direccion', 'estado_civil', 'numero_personas_cargo'),
            'description': 'Estos campos pueden ser editados para corregir datos errÃ³neos ingresados por error.'
        }),
        ('InformaciÃ³n del Negocio', {
            'fields': ('nombre_negocio', 'ubicacion_negocio', 'tiempo_operando', 'dias_trabajados_sem',
                      'prod_serv_ofrec', 'ingresos_prom_mes', 'cli_aten_day', 'inventario'),
            'classes': ('collapse',)
        }),
        ('Referencias', {
            'fields': ('nomb_ref_per1', 'cel_ref_per1', 'rel_ref_per1',
                      'nomb_ref_cl1', 'cel_ref_cl1', 'rel_ref_cl1', 'ref_conoc_lid_com'),
            'classes': ('collapse',)
        }),
        ('InformaciÃ³n Adicional', {
            'fields': ('tipo_cta_mno', 'ahorro_tand_alc', 'depend_h', 'desc_cred_nec',
                      'redes_soc', 'fotos_prod', 'desc_fotos_neg'),
            'classes': ('collapse',)
        }),
        ('EvaluaciÃ³n y Scoring', {
            'fields': ('puntaje', 'observaciones_analista', 'puntaje_imagenes', 'datos_scoring_imagenes'),
            'classes': ('collapse',)
        }),
    )

    def credito_numero(self, obj):
        return obj.credito.numero_credito
    credito_numero.short_description = 'NÃºmero de CrÃ©dito'
    credito_numero.admin_order_field = 'credito__numero_credito'

    def fecha_solicitud(self, obj):
        return obj.credito.fecha_solicitud
    fecha_solicitud.short_description = 'Fecha Solicitud'
    fecha_solicitud.admin_order_field = 'credito__fecha_solicitud'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('credito')

    actions = ['detectar_cedulas_invalidas']

    @admin.action(description='Detectar cÃ©dulas con datos no numÃ©ricos')
    def detectar_cedulas_invalidas(self, request, queryset):
        """
        Detecta y muestra los registros con cÃ©dulas que contienen datos no numÃ©ricos.
        """
        invalidos = []
        for obj in queryset:
            # Verificar si numero_cedula contiene caracteres no numÃ©ricos
            if not obj.numero_cedula.isdigit():
                invalidos.append(f"{obj.credito.numero_credito}: '{obj.nombre}' tiene cÃ©dula invÃ¡lida '{obj.numero_cedula}'")

        if invalidos:
            self.message_user(
                request,
                f"Se encontraron {len(invalidos)} registros con cÃ©dulas invÃ¡lidas:\n" + "\n".join(invalidos),
                level='warning'
            )
        else:
            self.message_user(request, "No se encontraron cÃ©dulas invÃ¡lidas en la selecciÃ³n.", level='success')


class EmpresaReferidaFilter(admin.SimpleListFilter):
    title = 'referido'
    parameter_name = 'referido'

    def lookups(self, request, model_admin):
        return (
            ('si', 'Referidas'),
            ('no', 'No referidas'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'si':
            return queryset.filter(asesor_comercial__isnull=False)
        if self.value() == 'no':
            return queryset.filter(asesor_comercial__isnull=True)
        return queryset


class EmpresaAdminForm(forms.ModelForm):
    fue_referida = forms.BooleanField(required=False, label='¿Fue referido?')
    asesor_comercial = forms.ModelChoiceField(
        queryset=AsesorComercial.objects.filter(activo=True).order_by('nombre'),
        required=False,
        label='Ejecutivo',
        help_text='Selecciona un ejecutivo ya creado. Este es el vínculo real de la empresa.',
    )
    asesor_nombre = forms.CharField(
        required=False,
        label='Nombre del ejecutivo (captura inicial)',
        help_text='Úsalo solo si el ejecutivo aún no existe en el sistema.',
    )
    asesor_cedula = forms.CharField(
        required=False,
        label='Cédula del ejecutivo (captura inicial)',
        help_text='Si no seleccionas un ejecutivo existente, estos datos crearán uno nuevo.',
    )

    class Meta:
        model = Empresa
        fields = '__all__'

    class Media:
        js = ('admin/js/empresa_referrals_admin.js',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        asesor = getattr(self.instance, 'asesor_comercial', None)
        if asesor:
            self.fields['fue_referida'].initial = True
            self.fields['asesor_comercial'].initial = asesor
            self.fields['asesor_nombre'].initial = asesor.nombre
            self.fields['asesor_cedula'].initial = asesor.cedula

    def clean(self):
        cleaned_data = super().clean()
        fue_referida = cleaned_data.get('fue_referida')
        asesor_existente = cleaned_data.get('asesor_comercial')
        asesor_nombre = (cleaned_data.get('asesor_nombre') or '').strip()
        asesor_cedula = (cleaned_data.get('asesor_cedula') or '').strip()

        if fue_referida and not asesor_existente and (not asesor_nombre or not asesor_cedula):
            raise ValidationError('Selecciona un ejecutivo existente o ingresa nombre y cédula para crearlo.')
        if not fue_referida:
            cleaned_data['asesor_comercial'] = None
            cleaned_data['asesor_nombre'] = ''
            cleaned_data['asesor_cedula'] = ''
        return cleaned_data

    def save(self, commit=True):
        empresa = super().save(commit=False)
        if self.cleaned_data.get('fue_referida'):
            asesor = self.cleaned_data.get('asesor_comercial')
            if not asesor:
                asesor, _ = AsesorComercial.objects.get_or_create(
                    cedula=self.cleaned_data['asesor_cedula'].strip(),
                    defaults={'nombre': self.cleaned_data['asesor_nombre'].strip()},
                )
                asesor.nombre = self.cleaned_data['asesor_nombre'].strip()
                asesor.activo = True
                asesor.save(update_fields=['nombre', 'activo'])
            empresa.asesor_comercial = asesor
        else:
            empresa.asesor_comercial = None
        if commit:
            empresa.save()
            self.save_m2m()
        return empresa



@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    form = EmpresaAdminForm
    list_display = (
        'nombre',
        'slug',
        'tipo_empresa',
        'referido_display',
        'asesor_display',
        'convenio_activo',
        'correo_contacto',
        'telefono_contacto',
        'marketplace_fee_percent',
        'pagos_habilitados',
    )
    search_fields = ('nombre', 'slug', 'nit', 'correo_contacto')
    list_filter = ('tipo_empresa', 'convenio_activo', 'pagos_habilitados', EmpresaReferidaFilter, 'asesor_comercial')
    readonly_fields = ('asesor_actual_resumen',)
    fieldsets = (
        ('Informacion base', {
            'fields': (
                'nombre', 'slug', 'razon_social', 'nit', 'representante_legal',
                'tipo_empresa', 'convenio_activo',
            )
        }),
        ('Origen comercial', {
            'fields': ('fue_referida', 'asesor_comercial', 'asesor_nombre', 'asesor_cedula', 'asesor_actual_resumen'),
            'description': 'Flujo recomendado: crear primero el Ejecutivo y luego vincularlo desde aquí. '
                           'La captura manual queda solo como respaldo inicial cuando el ejecutivo aún no existe.',
        }),
        ('Marketplace y contacto', {
            'fields': (
                'descripcion_marketplace', 'whatsapp_contacto', 'logo',
                'correo_contacto', 'telefono_contacto',
                'mp_user_id', 'mp_access_token', 'marketplace_fee_percent', 'pagos_habilitados',
            )
        }),
    )

    def referido_display(self, obj):
        return obj.fue_referida
    referido_display.boolean = True
    referido_display.short_description = '¿Fue referido?'

    def asesor_display(self, obj):
        return obj.asesor_comercial.nombre if obj.asesor_comercial_id else 'Sin ejecutivo'
    asesor_display.short_description = 'Ejecutivo'

    def asesor_actual_resumen(self, obj):
        if not obj or not obj.asesor_comercial_id:
            return 'Sin ejecutivo vinculado'
        asesor = obj.asesor_comercial
        return format_html(
            '<strong>{}</strong><br><span style="color:#64748b;">Cédula: {}</span>',
            asesor.nombre,
            asesor.cedula,
        )
    asesor_actual_resumen.short_description = 'Ejecutivo vinculado'


@admin.register(AsesorComercial)
class AsesorComercialAdmin(admin.ModelAdmin):
    list_display = (
        'nombre',
        'cedula',
        'email',
        'telefono',
        'activo',
        'empresas_count',
        'creditos_count',
        'monto_referido_display',
    )
    search_fields = ('nombre', 'cedula', 'email', 'telefono')
    list_filter = ('activo',)
    readonly_fields = ('creado_en', 'actualizado_en', 'performance_summary')
    autocomplete_fields = ('usuario',)
    actions = ['reenviar_invitacion_activacion']
    fieldsets = (
        ('Identidad', {
            'fields': ('nombre', 'cedula', 'usuario', 'activo')
        }),
        ('Contacto', {
            'fields': ('email', 'telefono', 'observaciones')
        }),
        ('Seguimiento', {
            'fields': ('performance_summary', 'creado_en', 'actualizado_en')
        }),
    )

    def save_model(self, request, obj, form, change):
        if obj.usuario and not obj.email:
            obj.email = obj.usuario.email
        super().save_model(request, obj, form, change)

        if not change:
            if not getattr(settings, 'EXECUTIVE_AUTO_SEND_ACTIVATION_ON_CREATE', True):
                self.message_user(
                    request,
                    'El ejecutivo fue creado sin envío automático. Usa la acción de reenvío cuando quieras habilitar el acceso.',
                    level=messages.INFO
                )
                return
            if not obj.email:
                self.message_user(
                    request,
                    f'El ejecutivo {obj.nombre} fue creado sin correo. No se pudo enviar la activación automáticamente.',
                    level=messages.WARNING
                )
                return
            try:
                enviar_invitacion_activacion_ejecutivo(obj, created_by=request.user)
                self.message_user(
                    request,
                    f'Se envió automáticamente la invitación de activación a {obj.email}.',
                    level=messages.SUCCESS
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f'El ejecutivo se creó, pero no se pudo enviar la invitación: {exc}',
                    level=messages.ERROR
                )

    def empresas_count(self, obj):
        return obj.empresas_referidas.count()
    empresas_count.short_description = 'Empresas'

    def creditos_count(self, obj):
        return get_asesor_performance_snapshot(obj)['total_creditos']
    creditos_count.short_description = 'Créditos colocados'

    def monto_referido_display(self, obj):
        return get_asesor_performance_snapshot(obj)['monto_colocado']
    monto_referido_display.short_description = 'Monto colocado'

    def performance_summary(self, obj):
        summary = get_asesor_performance_snapshot(obj)
        return format_html(
            '<strong>Empresas:</strong> {}<br>'
            '<strong>Créditos activos:</strong> {}<br>'
            '<strong>Pagados:</strong> {}<br>'
            '<strong>Monto colocado:</strong> ${:,.0f}<br>'
            '<strong>Comisión acumulada:</strong> ${:,.0f}<br>'
            '<strong>Saldo de cartera:</strong> ${:,.0f}',
            summary['empresas_count'],
            summary['creditos_activos'],
            summary['creditos_pagados'],
            summary['monto_colocado'],
            summary['comision_acumulada'],
            summary['saldo_cartera'],
        )
    performance_summary.short_description = 'Resumen comercial'

    @admin.action(description='Enviar o reenviar invitación de activación')
    def reenviar_invitacion_activacion(self, request, queryset):
        enviados = 0
        errores = 0

        for asesor in queryset:
            try:
                enviar_invitacion_activacion_ejecutivo(asesor, created_by=request.user)
                enviados += 1
            except Exception as exc:
                errores += 1
                self.message_user(
                    request,
                    f'No se pudo enviar la invitación a {asesor.nombre}: {exc}',
                    level=messages.ERROR
                )

        if enviados:
            self.message_user(
                request,
                f'Se enviaron {enviados} invitaciones de activación.',
                level=messages.SUCCESS
            )
        if errores:
            self.message_user(
                request,
                f'Hubo {errores} errores durante el envío.',
                level=messages.WARNING
            )


@admin.register(VinculoLaboralEmpresa)
class VinculoLaboralEmpresaAdmin(admin.ModelAdmin):
    list_display = ('nombre_empleado', 'empresa', 'documento_empleado', 'estado_vinculo', 'fecha_alta_aprobado', 'salario_base_mensual', 'validado_por_pagador')
    list_filter = ('estado_vinculo', 'validado_por_pagador', 'empresa')
    search_fields = ('nombre_empleado', 'documento_empleado', 'correo_empleado', 'empresa__nombre')
    readonly_fields = ('creado_en', 'actualizado_en')


@admin.register(CreditoAdelantoNomina)
class CreditoAdelantoNominaAdmin(admin.ModelAdmin):
    list_display = ('credito', 'vinculo_laboral', 'monto_solicitado', 'monto_maximo_calculado', 'dias_adelanto')
    list_filter = ('dias_adelanto', 'vinculo_laboral__empresa')
    search_fields = ('credito__numero_credito', 'vinculo_laboral__nombre_empleado', 'vinculo_laboral__documento_empleado')
    readonly_fields = ('creado_en', 'actualizado_en')


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


class MarketplacePedidoItemInline(admin.TabularInline):
    model = MarketplacePedidoItem
    extra = 0


@admin.register(MarketplacePedido)
class MarketplacePedidoAdmin(admin.ModelAdmin):
    list_display = ('numero_pedido', 'empresa', 'comprador_email', 'estado', 'total', 'created_at')
    list_filter = ('estado', 'empresa', 'created_at')
    search_fields = ('numero_pedido', 'comprador_email', 'comprador_nombre', 'empresa__nombre')
    readonly_fields = ('numero_pedido', 'created_at', 'updated_at')
    inlines = [MarketplacePedidoItemInline]


@admin.register(MarketplacePago)
class MarketplacePagoAdmin(admin.ModelAdmin):
    list_display = ('pedido', 'proveedor', 'estado', 'amount_gross', 'amount_net', 'paid_at')
    list_filter = ('proveedor', 'estado', 'created_at')
    search_fields = ('pedido__numero_pedido', 'provider_payment_id', 'external_reference')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(MarketplaceDireccionEntrega)
class MarketplaceDireccionEntregaAdmin(admin.ModelAdmin):
    list_display = ('pedido', 'nombre_contacto', 'ciudad', 'telefono_contacto')
    search_fields = ('pedido__numero_pedido', 'nombre_contacto', 'ciudad', 'telefono_contacto')


@admin.register(MarketplaceLiquidacionEmpresa)
class MarketplaceLiquidacionEmpresaAdmin(admin.ModelAdmin):
    list_display = ('pedido', 'empresa', 'estado', 'valor_bruto', 'marketplace_fee_amount', 'valor_neto', 'paid_at')
    list_filter = ('estado', 'empresa', 'created_at')
    search_fields = ('pedido__numero_pedido', 'empresa__nombre', 'external_reference')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(HistorialPago)
class HistorialPagoAdmin(admin.ModelAdmin):
    list_display = ('credito', 'fecha_aplicacion', 'monto', 'estado', 'metodo_pago', 'origen_registro', 'referencia_pago')
    list_filter = ('estado', 'metodo_pago', 'origen_registro', 'fecha_aplicacion')
    search_fields = ('credito__numero_credito', 'referencia_pago')


@admin.register(LotePagoEmpresa)
class LotePagoEmpresaAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'nombre_original', 'estado', 'pagos_aplicados', 'errores_count', 'creado_en')
    list_filter = ('estado', 'creado_en', 'empresa')
    search_fields = ('nombre_original', 'checksum', 'empresa__nombre')
    readonly_fields = ('checksum', 'creado_en')

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


@admin.register(InvestorAccount)
class InvestorAccountAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'moneda', 'activa', 'fecha_apertura', 'password_configurado')
    list_filter = ('activa', 'moneda')
    search_fields = ('usuario__username', 'usuario__email')
    readonly_fields = ('fecha_apertura', 'fecha_actualizacion')
    actions = ['enviar_invitacion_activacion']

    def password_configurado(self, obj):
        return obj.usuario.has_usable_password()
    password_configurado.boolean = True
    password_configurado.short_description = 'Password configurado'

    @admin.action(description='Enviar invitacion de activacion a inversionistas seleccionados')
    def enviar_invitacion_activacion(self, request, queryset):
        enviados = 0
        errores = 0
        for account in queryset.select_related('usuario'):
            try:
                enviar_invitacion_inversionista(account.usuario, created_by=request.user)
                enviados += 1
            except Exception:
                errores += 1
        if enviados:
            self.message_user(request, f'Se enviaron {enviados} invitaciones de activacion.', level=messages.SUCCESS)
        if errores:
            self.message_user(request, f'No se pudieron enviar {errores} invitaciones.', level=messages.WARNING)


@admin.register(InvestmentPosition)
class InvestmentPositionAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'account', 'titulo', 'estado', 'aporte_inicial', 'capital_activo', 'capital_recuperado', 'fecha_inicio')
    list_filter = ('estado', 'fecha_inicio')
    search_fields = ('referencia', 'titulo', 'account__usuario__email')
    readonly_fields = ('referencia', 'created_at', 'updated_at')


@admin.register(InvestmentCashflow)
class InvestmentCashflowAdmin(admin.ModelAdmin):
    list_display = ('position', 'tipo', 'monto', 'fecha_efectiva', 'created_at')
    list_filter = ('tipo', 'fecha_efectiva')
    search_fields = ('position__referencia', 'position__account__usuario__email', 'descripcion')
    readonly_fields = ('created_at',)


@admin.register(InvestmentReturnSnapshot)
class InvestmentReturnSnapshotAdmin(admin.ModelAdmin):
    list_display = ('account', 'fecha_corte', 'roi_acumulado', 'roi_mensual', 'capital_activo', 'capital_recuperado')
    list_filter = ('fecha_corte',)
    search_fields = ('account__usuario__email',)
    readonly_fields = ('created_at',)


@admin.register(InvestmentEvent)
class InvestmentEventAdmin(admin.ModelAdmin):
    list_display = ('account', 'position', 'titulo', 'fecha_evento')
    list_filter = ('fecha_evento',)
    search_fields = ('account__usuario__email', 'position__referencia', 'titulo')
    readonly_fields = ('fecha_evento',)

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
        ('InformaciÃ³n General', {
            'fields': ('usuario', 'tipo', 'titulo', 'mensaje')
        }),
        ('Estado', {
            'fields': ('leida', 'fecha_creacion', 'fecha_leida')
        }),
        ('AcciÃ³n', {
            'fields': ('url',),
            'description': 'URL a la que redirige cuando el usuario hace clic en la notificaciÃ³n'
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('usuario')


#? ----- ADMINISTRACIÃ“N DE PAGARÃ‰S (ZapSign) -----
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
        ('InformaciÃ³n del PagarÃ©', {
            'fields': ('numero_pagare', 'credito', 'estado', 'version_plantilla')
        }),
        ('Archivos PDF', {
            'fields': ('archivo_pdf', 'archivo_pdf_firmado', 'hash_pdf')
        }),
        ('IntegraciÃ³n ZapSign', {
            'fields': ('zapsign_doc_token', 'zapsign_sign_url', 'zapsign_signed_file_url', 'zapsign_status'),
            'classes': ('collapse',)
        }),
        ('Fechas y AuditorÃ­a', {
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
        ('InformaciÃ³n del Evento', {
            'fields': ('doc_token', 'event', 'received_at', 'ip_address')
        }),
        ('ValidaciÃ³n', {
            'fields': ('signature_valid', 'processed', 'error_message')
        }),
        ('Payload Completo', {
            'fields': ('payload', 'headers'),
            'classes': ('collapse',),
            'description': 'Datos completos del webhook para auditorÃ­a'
        }),
    )

    def has_add_permission(self, request):
        # No permitir crear webhooks manualmente
        return False

    def has_change_permission(self, request, obj=None):
        # Solo lectura
        return False

