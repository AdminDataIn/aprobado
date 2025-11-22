from django.contrib import admin
from .models import Credito, CreditoEmprendimiento, CreditoLibranza, Empresa, HistorialPago, CuentaAhorro, MovimientoAhorro, ConfiguracionTasaInteres, ImagenNegocio
from django.utils import timezone
from datetime import timedelta

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
    #! Hacemos los campos de solicitud readonly una vez creados
    readonly_fields = ('nombre', 'numero_cedula', 'fecha_nac', 'celular_wh', 'direccion', 'estado_civil', 'numero_personas_cargo', 'nombre_negocio', 'ubicacion_negocio', 'tiempo_operando', 'dias_trabajados_sem', 'prod_serv_ofrec', 'ingresos_prom_mes', 'cli_aten_day', 'inventario', 'nomb_ref_per1', 'cel_ref_per1', 'rel_ref_per1', 'nomb_ref_cl1', 'cel_ref_cl1', 'rel_ref_cl1', 'ref_conoc_lid_com', 'foto_negocio', 'desc_fotos_neg', 'tipo_cta_mno', 'ahorro_tand_alc', 'depend_h', 'desc_cred_nec', 'redes_soc', 'fotos_prod', 'puntaje', 'puntaje_imagenes', 'datos_scoring_imagenes')
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

@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ('nombre',)
    search_fields = ('nombre',)

@admin.register(HistorialPago)
class HistorialPagoAdmin(admin.ModelAdmin):
    list_display = ('credito', 'fecha_pago', 'monto', 'estado', 'referencia_pago')
    list_filter = ('estado', 'fecha_pago')
    search_fields = ('credito__numero_credito', 'referencia_pago')

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