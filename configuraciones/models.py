from django.db import models
from django.core.validators import FileExtensionValidator

class SolicitudCredito(models.Model):
    # Información personal
    valor_credito = models.DecimalField(max_digits=10, decimal_places=2)
    plazo = models.IntegerField()
    nombre = models.CharField(max_length=100)
    numero_cedula = models.CharField(max_length=20)
    fecha_nac = models.DateField()
    celular_wh = models.CharField(max_length=20)
    direccion = models.TextField()
    estado_civil = models.CharField(max_length=20)
    numero_personas_cargo = models.IntegerField()

    # Información del negocio
    nombre_negocio = models.CharField(max_length=100)
    ubicacion_negocio = models.TextField()
    tiempo_operando = models.CharField(max_length=50)
    dias_trabajados_sem = models.IntegerField()
    prod_serv_ofrec = models.TextField()
    ingresos_prom_mes = models.CharField(max_length=50)
    cli_aten_day = models.IntegerField()
    inventario = models.CharField(max_length=2)

    # Referencias
    nomb_ref_per1 = models.CharField(max_length=100)
    cel_ref_per1 = models.CharField(max_length=20)
    rel_ref_per1 = models.CharField(max_length=50)
    nomb_ref_cl1 = models.CharField(max_length=100)
    cel_ref_cl1 = models.CharField(max_length=20)
    rel_ref_cl1 = models.CharField(max_length=50)
    ref_conoc_lid_com = models.CharField(max_length=2)

    # Archivos
    #foto_negocio = models.ImageField(upload_to='fotos_negocios/')
    foto_negocio = models.FileField(
        upload_to='fotos_negocios/',
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        help_text="Solo se permiten archivos PDF"
    )
    desc_fotos_neg = models.TextField()

    # Otros campos
    tipo_cta_mno = models.CharField(max_length=20)
    ahorro_tand_alc = models.CharField(max_length=2)
    depend_h = models.CharField(max_length=2)
    desc_cred_nec = models.TextField()
    redes_soc = models.CharField(max_length=2)
    fotos_prod = models.CharField(max_length=2)

    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, default='pendiente')

    def __str__(self):
        return f"{self.nombre} - {self.valor_credito}"


class ConfiguracionPeso(models.Model):
    parametro = models.CharField(max_length=255)
    nivel = models.CharField(max_length=255)
    estimacion = models.IntegerField()

    def __str__(self):
        return f"Configuración - Parámetro: {self.parametro}, Nivel: {self.nivel}"