from django.db import models
from django.conf import settings
from gestion_creditos.models import Empresa


#! MODELO DE PERFIL DE PAGADOR PARA EL USUARIO ADMIN DE PAGOS
class PerfilPagador(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='perfil_pagador')
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    es_pagador = models.BooleanField(default=True)

    def __str__(self):
        return f"Pagador: {self.usuario.username} de {self.empresa.nombre}"