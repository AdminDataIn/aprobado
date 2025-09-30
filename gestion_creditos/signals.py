from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from decimal import Decimal

from .models import Credito, HistorialEstado


@receiver(pre_save, sender=Credito)
def registrar_cambio_estado(sender, instance, **kwargs):
    """
    Antes de guardar un crédito, verifica si el estado ha cambiado y, si es así,
    guarda un registro en el HistorialEstado.
    """
    if instance.pk:  # Si el objeto ya existe en la BD
        try:
            credito_anterior = Credito.objects.get(pk=instance.pk)
            if credito_anterior.estado != instance.estado:
                # El estado ha cambiado, crea un registro en el historial
                # Nota: El usuario que modifica no se captura aquí, se debería pasar desde la vista.
                HistorialEstado.objects.create(
                    credito=instance,
                    estado_anterior=credito_anterior.estado,
                    estado_nuevo=instance.estado,
                    motivo=f"Cambio de estado a {instance.get_estado_display()}"
                )
        except Credito.DoesNotExist:
            pass  # El objeto es nuevo, no hay estado anterior


@receiver(post_save, sender=Credito)
def calcular_detalles_credito_activo(sender, instance, created, **kwargs):
    """
    Cuando un crédito cambia al estado 'ACTIVO', calcula y guarda los detalles
    financieros como el saldo, la cuota y la próxima fecha de pago.
    """
    if instance.estado == Credito.EstadoCredito.ACTIVO and instance.pk:
        # Determinar si es un crédito recién activado
        historial_relevante = HistorialEstado.objects.filter(
            credito=instance, 
            estado_nuevo=Credito.EstadoCredito.ACTIVO
        ).order_by('-fecha').first()

        # Solo actuar si el estado anterior no era también ACTIVO (evita recalcular en cada guardado)
        if historial_relevante and historial_relevante.estado_anterior != Credito.EstadoCredito.ACTIVO:
            
            detalle_credito = getattr(instance, 'detalle_emprendimiento', None) or getattr(instance, 'detalle_libranza', None)

            # --- VALIDACIÓN DE ROBUSTEZ ---
            # Si no hay detalle, monto o plazo, no se pueden hacer cálculos.
            if not detalle_credito or not detalle_credito.monto_aprobado or not detalle_credito.plazo or detalle_credito.plazo <= 0:
                # Opcional: Registrar un log de advertencia aquí si es necesario
                # logger.warning(f"No se pudieron calcular los detalles para el crédito {instance.id} por falta de datos.")
                return # Salir de la función para evitar errores

            # 1. Calcular Total a Pagar y Cuota según lógica de Comisión + IVA
            monto_aprobado = detalle_credito.monto_aprobado
            plazo = detalle_credito.plazo

            # Porcentajes definidos por el usuario (temporalmente hardcodeados)
            porcentaje_comision = Decimal('0.10')
            porcentaje_iva = Decimal('0.19')

            monto_comision = monto_aprobado * porcentaje_comision
            monto_iva = monto_comision * porcentaje_iva
            total_a_pagar = monto_aprobado + monto_comision + monto_iva

            if plazo > 0:
                valor_cuota = total_a_pagar / plazo
            else:
                valor_cuota = total_a_pagar
            
            detalle_credito.valor_cuota = valor_cuota.quantize(Decimal('0.01'))

            # 2. Establecer Saldo Pendiente inicial como el Total a Pagar
            detalle_credito.saldo_pendiente = total_a_pagar.quantize(Decimal('0.01'))

            # 3. Lógica de Próximo Vencimiento
            hoy = timezone.now().date()
            if hoy.day <= 15:
                # Primer pago el 1ro del mes siguiente
                fecha_pago = (hoy.replace(day=1) + relativedelta(months=1))
            else:
                # Primer pago el 1ro del mes subsiguiente
                fecha_pago = (hoy.replace(day=1) + relativedelta(months=2))
            
            detalle_credito.fecha_proximo_pago = fecha_pago

            detalle_credito.save()
