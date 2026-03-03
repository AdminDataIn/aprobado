from django.db import models
from django.conf import settings
from gestion_creditos.models import Empresa
from django.contrib.auth.models import User
from django.utils import timezone


#! MODELO DE PERFIL DE PAGADOR PARA EL USUARIO ADMIN DE PAGOS
class PerfilPagador(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='perfil_pagador')
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    es_pagador = models.BooleanField(default=True)

    def __str__(self):
        return f"Pagador: {self.usuario.username} de {self.empresa.nombre}"


class PerfilEmpresaMarketing(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='perfil_marketing')
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"Marketing: {self.usuario.username} de {self.empresa.nombre}"
    
class PerfilUsuario(models.Model):
    """Perfil extendido para todos los usuarios"""
    TIPO_USUARIO_CHOICES = [
        ('INVERSIONISTA', 'Inversionista'),
        ('EMPRENDEDOR', 'Emprendedor'),
        ('EMPLEADO', 'Empleado'),
        ('ADMIN', 'Administrador'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    tipo_usuario = models.CharField(max_length=20, choices=TIPO_USUARIO_CHOICES)
    documento_identidad = models.CharField(max_length=20, unique=True)
    telefono = models.CharField(max_length=20)
    
    # Para inversionistas (datos mínimos)
    acepta_terminos = models.BooleanField(default=False)
    verificado = models.BooleanField(default=False)


class PagadorAccessToken(models.Model):
    class TipoToken(models.TextChoices):
        ACTIVACION = 'activacion', 'Activacion'
        RESET_PASSWORD = 'reset_password', 'Reset password'

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pagador_access_tokens'
    )
    perfil_pagador = models.ForeignKey(
        PerfilPagador,
        on_delete=models.CASCADE,
        related_name='access_tokens'
    )
    tipo = models.CharField(max_length=30, choices=TipoToken.choices, default=TipoToken.ACTIVACION)
    token_hash = models.CharField(max_length=64, unique=True)
    token_hint = models.CharField(max_length=12, blank=True)
    email_destino = models.EmailField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagador_tokens_creados'
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['usuario', 'tipo'], name='pag_token_user_tipo_idx'),
            models.Index(fields=['expires_at'], name='pag_token_exp_idx'),
        ]

    def __str__(self):
        return f"{self.usuario.username} - {self.tipo} - {self.email_destino}"

    @property
    def esta_vigente(self):
        return not self.used_at and not self.invalidated_at and self.expires_at > timezone.now()
