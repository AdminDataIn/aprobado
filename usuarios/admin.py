from django.contrib import admin
from django.contrib import messages

from .models import PerfilPagador, PerfilEmpresaMarketing, PagadorAccessToken
from .pagador_activation_service import enviar_invitacion_activacion_pagador

@admin.register(PerfilPagador)
class PerfilPagadorAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'empresa', 'es_pagador', 'usuario_activo')
    list_filter = ('empresa', 'es_pagador')
    search_fields = ('usuario__username', 'usuario__email', 'empresa__nombre')
    actions = ['reenviar_invitacion_activacion']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        # Solo enviamos invitacion automaticamente cuando el perfil es nuevo.
        # Si el usuario ya tenia acceso previo, el servicio no lo bloquea de
        # forma retroactiva. Si falta correo, dejamos mensaje y no rompemos.
        if not change:
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
