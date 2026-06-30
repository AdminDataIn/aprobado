from django.contrib import admin
from django.contrib import messages
from django.contrib.admin import SimpleListFilter
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User
from django.contrib.admin.sites import NotRegistered
from django.conf import settings

from .models import ExecutiveAccessToken, PerfilPagador, PerfilEmpresaMarketing, PagadorAccessToken, ProductAccessProfile
from .pagador_activation_service import enviar_invitacion_activacion_pagador


class UserRoleFilter(SimpleListFilter):
    title = 'rol operativo'
    parameter_name = 'rol_operativo'

    def lookups(self, request, model_admin):
        return (
            ('staff', 'Administradores'),
            ('pagador', 'Pagadores'),
            ('ejecutivo', 'Ejecutivos'),
            ('inversionista', 'Inversionistas'),
            ('marketplace_admin', 'Admins marketplace'),
            ('libranza', 'Clientes libranza'),
            ('emprendimiento', 'Clientes emprendimiento'),
            ('marketplace_buyer', 'Compradores marketplace'),
            ('sin_clasificar', 'Sin clasificar'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'staff':
            return queryset.filter(is_staff=True)
        if value == 'pagador':
            return queryset.filter(perfil_pagador__isnull=False)
        if value == 'ejecutivo':
            return queryset.filter(asesor_comercial__isnull=False)
        if value == 'inversionista':
            return queryset.filter(investor_account__isnull=False)
        if value == 'marketplace_admin':
            return queryset.filter(perfil_marketing__isnull=False)
        if value == 'libranza':
            return queryset.filter(product_access_profile__flow=ProductAccessProfile.ProductFlow.LIBRANZA)
        if value == 'emprendimiento':
            return queryset.filter(product_access_profile__flow=ProductAccessProfile.ProductFlow.EMPRENDIMIENTO)
        if value == 'marketplace_buyer':
            return queryset.filter(product_access_profile__flow=ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER)
        if value == 'sin_clasificar':
            return queryset.filter(
                perfil_pagador__isnull=True,
                perfil_marketing__isnull=True,
                investor_account__isnull=True,
                product_access_profile__isnull=True,
                is_staff=False,
                is_superuser=False,
            )
        return queryset


class ProductFlowFilter(SimpleListFilter):
    title = 'flujo principal'
    parameter_name = 'flujo_principal'

    def lookups(self, request, model_admin):
        return ProductAccessProfile.ProductFlow.choices

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(product_access_profile__flow=self.value())
        return queryset


try:
    admin.site.unregister(User)
except NotRegistered:
    pass


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        'username',
        'email',
        'first_name',
        'last_name',
        'role_summary',
        'flow_summary',
        'is_staff',
        'is_active',
        'last_login',
    )
    list_filter = (
        'is_staff',
        'is_superuser',
        'is_active',
        UserRoleFilter,
        ProductFlowFilter,
    )
    search_fields = (
        'username',
        'email',
        'first_name',
        'last_name',
        'perfil_pagador__empresa__nombre',
        'perfil_marketing__empresa__nombre',
    )
    ordering = ('-is_staff', 'username')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'perfil_pagador',
            'perfil_marketing',
            'product_access_profile',
            'investor_account',
        )

    @admin.display(description='Roles')
    def role_summary(self, obj):
        roles = []
        if obj.is_superuser:
            roles.append('Superadmin')
        elif obj.is_staff:
            roles.append('Admin')
        if hasattr(obj, 'perfil_pagador'):
            roles.append('Pagador')
        if hasattr(obj, 'asesor_comercial'):
            roles.append('Ejecutivo')
        if hasattr(obj, 'perfil_marketing'):
            roles.append('Marketplace admin')
        if hasattr(obj, 'investor_account'):
            roles.append('Inversionista')
        if not roles:
            roles.append('Usuario base')
        return ' / '.join(roles)

    @admin.display(description='Flujo')
    def flow_summary(self, obj):
        profile = getattr(obj, 'product_access_profile', None)
        return profile.get_flow_display() if profile else 'Sin flujo'

@admin.register(PerfilPagador)
class PerfilPagadorAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'empresa', 'es_pagador', 'nivel_aprobacion_libranza', 'usuario_activo')
    list_filter = ('empresa', 'es_pagador', 'nivel_aprobacion_libranza')
    search_fields = ('usuario__username', 'usuario__email', 'empresa__nombre')
    actions = ['reenviar_invitacion_activacion']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        # Solo enviamos invitacion automaticamente cuando el perfil es nuevo.
        # Si el usuario ya tenia acceso previo, el servicio no lo bloquea de
        # forma retroactiva. Si falta correo, dejamos mensaje y no rompemos.
        if not change:
            if not getattr(settings, 'PAGADOR_AUTO_SEND_ACTIVATION_ON_CREATE', True):
                self.message_user(
                    request,
                    'El pagador fue creado sin envio automatico. Usa la accion de reenvio cuando quieras habilitar el acceso.',
                    level=messages.INFO
                )
                return
            usuario = obj.usuario
            if not usuario.email:
                self.message_user(
                    request,
                    f'El pagador {usuario.username} fue creado sin correo. No se pudo enviar la invitacion automaticamente.',
                    level=messages.WARNING
                )
                return
            try:
                enviar_invitacion_activacion_pagador(obj, created_by=request.user)
                self.message_user(
                    request,
                    f'Se envio automaticamente la invitacion de activacion a {usuario.email}.',
                    level=messages.SUCCESS
                )
            except Exception as exc:
                self.message_user(
                    request,
                    f'El perfil se creo, pero no se pudo enviar la invitacion a {usuario.username}: {exc}',
                    level=messages.ERROR
                )

    def usuario_activo(self, obj):
        return obj.usuario.is_active
    usuario_activo.boolean = True
    usuario_activo.short_description = 'Usuario activo'

    @admin.action(description='Enviar o reenviar invitacion de activacion')
    def reenviar_invitacion_activacion(self, request, queryset):
        enviados = 0
        errores = 0

        for perfil in queryset.select_related('usuario', 'empresa'):
            try:
                enviar_invitacion_activacion_pagador(perfil, created_by=request.user)
                enviados += 1
            except Exception as exc:
                errores += 1
                self.message_user(
                    request,
                    f'No se pudo enviar invitacion a {perfil.usuario.username}: {exc}',
                    level=messages.ERROR
                )

        if enviados:
            self.message_user(
                request,
                f'Se enviaron {enviados} invitaciones de activacion.',
                level=messages.SUCCESS
            )
        if errores:
            self.message_user(
                request,
                f'Hubo {errores} errores durante el envio.',
                level=messages.WARNING
            )


@admin.register(PerfilEmpresaMarketing)
class PerfilEmpresaMarketingAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'empresa', 'activo')
    list_filter = ('empresa', 'activo')
    search_fields = ('usuario__username', 'empresa__nombre')


@admin.register(PagadorAccessToken)
class PagadorAccessTokenAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'perfil_pagador', 'tipo', 'email_destino', 'created_at', 'expires_at', 'used_at', 'invalidated_at')
    list_filter = ('tipo', 'created_at', 'expires_at', 'used_at', 'invalidated_at')
    search_fields = ('usuario__username', 'usuario__email', 'perfil_pagador__empresa__nombre', 'token_hint')
    readonly_fields = (
        'usuario',
        'perfil_pagador',
        'tipo',
        'email_destino',
        'expires_at',
        'created_by',
        'token_hash',
        'token_hint',
        'created_at',
        'used_at',
        'invalidated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Queda como bitacora auditable. No se edita manualmente.
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return False


@admin.register(ExecutiveAccessToken)
class ExecutiveAccessTokenAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'asesor', 'tipo', 'email_destino', 'created_at', 'expires_at', 'used_at', 'invalidated_at')
    list_filter = ('tipo', 'created_at', 'expires_at', 'used_at', 'invalidated_at')
    search_fields = ('usuario__username', 'usuario__email', 'asesor__nombre', 'asesor__cedula', 'token_hint')
    readonly_fields = (
        'usuario',
        'asesor',
        'tipo',
        'email_destino',
        'expires_at',
        'created_by',
        'token_hash',
        'token_hint',
        'created_at',
        'used_at',
        'invalidated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return False


@admin.register(ProductAccessProfile)
class ProductAccessProfileAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'flow', 'locked_at', 'updated_at')
    list_filter = ('flow',)
    search_fields = ('usuario__username', 'usuario__email')
    readonly_fields = ('locked_at', 'updated_at')
