from django.db import models

class ConfiguracionPeso(models.Model):
    parametro = models.CharField(max_length=255)
    nivel = models.CharField(max_length=255)
    estimacion = models.IntegerField()

    def __str__(self):
        return f"Configuración - Parámetro: {self.parametro}, Nivel: {self.nivel}"