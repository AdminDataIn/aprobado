from django.db import models
from django.conf import settings
from gestion_creditos.models import Empresa
from django.contrib.auth.models import User


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
