"""
Tareas asíncronas de Celery para el sistema de créditos.

Este módulo contiene todas las tareas que se ejecutan de forma automática:
- Marcar créditos en mora
- Enviar recordatorios de pago
- Enviar alertas de mora
- Generar reportes automáticos
"""
import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from .models import Credito
from .credit_services import marcar_creditos_en_mora, gestionar_cambio_estado_credito
from .email_service import (
    enviar_recordatorio_pago,
    enviar_alerta_mora,
    enviar_notificacion_cambio_estado
)

logger = logging.getLogger(__name__)


@shared_task(name='gestion_creditos.tasks.marcar_creditos_en_mora_task')
def marcar_creditos_en_mora_task():
    """
    Tarea programada que marca automáticamente los créditos en mora.

    Se ejecuta diariamente a las 6:00 AM (configurado en celery.py).
    Busca créditos activos con fecha de pago vencida y los marca como EN_MORA.

    Returns:
        dict: Resultados de la ejecución con cantidad de créditos actualizados
    """
    logger.info("Iniciando tarea: Marcar créditos en mora")

    try:
        creditos_actualizados = marcar_creditos_en_mora()

        logger.info(
            f"Tarea completada: {creditos_actualizados} créditos marcados en mora"
        )

        return {
            'status': 'success',
            'creditos_actualizados': creditos_actualizados,
            'timestamp': timezone.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error en tarea marcar_creditos_en_mora_task: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': timezone.now().isoformat()
        }


@shared_task(name='gestion_creditos.tasks.enviar_recordatorios_pago_task')
def enviar_recordatorios_pago_task():
    """
    Tarea programada que envía recordatorios de pago a clientes.

    Se ejecuta diariamente a las 8:00 AM (configurado en celery.py).
    Envía recordatorios a clientes cuya cuota vence en 3 o 7 días.

    Returns:
        dict: Resultados con cantidad de recordatorios enviados
    """
    logger.info("Iniciando tarea: Enviar recordatorios de pago")

    try:
        hoy = timezone.now().date()
        recordatorios_enviados = 0

        # Definir días de anticipación para enviar recordatorios
        dias_recordatorio = [3, 7]  # Recordar 7 días antes y 3 días antes

        for dias in dias_recordatorio:
            fecha_objetivo = hoy + timedelta(days=dias)

            # Buscar créditos activos que vencen en esa fecha
            creditos = Credito.objects.filter(
                estado=Credito.EstadoCredito.ACTIVO,
                fecha_proximo_pago=fecha_objetivo
            ).select_related('usuario')

            for credito in creditos:
                try:
                    exito = enviar_recordatorio_pago(credito, dias)
                    if exito:
                        recordatorios_enviados += 1
                        logger.info(
                            f"Recordatorio enviado a {credito.usuario.email} "
                            f"para crédito {credito.numero_credito} ({dias} días)"
                        )
                except Exception as e:
                    logger.error(
                        f"Error al enviar recordatorio para crédito "
                        f"{credito.numero_credito}: {e}"
                    )

        logger.info(f"Tarea completada: {recordatorios_enviados} recordatorios enviados")

        return {
            'status': 'success',
            'recordatorios_enviados': recordatorios_enviados,
            'timestamp': timezone.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error en tarea enviar_recordatorios_pago_task: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': timezone.now().isoformat()
        }


@shared_task(name='gestion_creditos.tasks.enviar_alertas_mora_task')
def enviar_alertas_mora_task():
    """
    Tarea programada que envía alertas a clientes con créditos en mora.

    Se ejecuta diariamente a las 9:00 AM (configurado en celery.py).
    Envía alertas escalonadas según los días de mora (1, 7, 15, 30 días).

    Returns:
        dict: Resultados con cantidad de alertas enviadas
    """
    logger.info("Iniciando tarea: Enviar alertas de mora")

    try:
        alertas_enviadas = 0

        # Buscar todos los créditos en mora
        creditos_mora = Credito.objects.filter(
            estado=Credito.EstadoCredito.EN_MORA
        ).select_related('usuario')

        for credito in creditos_mora:
            try:
                dias_mora = credito.dias_en_mora

                # Enviar alerta solo en días específicos para no saturar al cliente
                # Alertas en: día 1, 7, 15, 30 y luego cada 30 días
                if dias_mora in [1, 7, 15, 30] or (dias_mora > 30 and dias_mora % 30 == 0):
                    exito = enviar_alerta_mora(credito, dias_mora)
                    if exito:
                        alertas_enviadas += 1
                        logger.info(
                            f"Alerta de mora enviada a {credito.usuario.email} "
                            f"para crédito {credito.numero_credito} ({dias_mora} días)"
                        )

            except Exception as e:
                logger.error(
                    f"Error al enviar alerta de mora para crédito "
                    f"{credito.numero_credito}: {e}"
                )

        logger.info(f"Tarea completada: {alertas_enviadas} alertas de mora enviadas")

        return {
            'status': 'success',
            'alertas_enviadas': alertas_enviadas,
            'timestamp': timezone.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error en tarea enviar_alertas_mora_task: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': timezone.now().isoformat()
        }


@shared_task(name='gestion_creditos.tasks.enviar_notificacion_cambio_estado_async')
def enviar_notificacion_cambio_estado_async(credito_id, nuevo_estado, motivo=""):
    """
    Tarea asíncrona para enviar notificación de cambio de estado.

    Esta tarea se puede llamar desde cualquier parte del código para enviar
    emails de forma asíncrona sin bloquear la ejecución.

    Args:
        credito_id (int): ID del crédito
        nuevo_estado (str): Nuevo estado del crédito
        motivo (str): Motivo del cambio de estado

    Returns:
        dict: Resultado de la ejecución
    """
    try:
        credito = Credito.objects.get(id=credito_id)
        exito = enviar_notificacion_cambio_estado(credito, nuevo_estado, motivo)

        if exito:
            logger.info(
                f"Notificación de cambio de estado enviada para crédito "
                f"{credito.numero_credito}: {nuevo_estado}"
            )
            return {'status': 'success', 'credito_id': credito_id}
        else:
            logger.warning(
                f"No se pudo enviar notificación para crédito {credito.numero_credito}"
            )
            return {'status': 'failed', 'credito_id': credito_id}

    except Credito.DoesNotExist:
        logger.error(f"Crédito con ID {credito_id} no existe")
        return {'status': 'error', 'error': 'Crédito no encontrado'}
    except Exception as e:
        logger.error(f"Error al enviar notificación para crédito {credito_id}: {e}")
        return {'status': 'error', 'error': str(e)}


@shared_task(name='gestion_creditos.tasks.generar_reporte_cartera_mensual')
def generar_reporte_cartera_mensual():
    """
    Tarea programada que genera reporte mensual de cartera.

    Se ejecuta el primer día de cada mes (se puede configurar en celery.py).
    Genera estadísticas consolidadas del mes anterior.

    Returns:
        dict: Estadísticas del mes
    """
    logger.info("Iniciando tarea: Generar reporte mensual de cartera")

    try:
        hoy = timezone.now().date()
        # Obtener estadísticas del mes anterior
        primer_dia_mes_anterior = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1)
        ultimo_dia_mes_anterior = hoy.replace(day=1) - timedelta(days=1)

        # Créditos desembolsados en el mes
        creditos_desembolsados = Credito.objects.filter(
            fecha_desembolso__date__gte=primer_dia_mes_anterior,
            fecha_desembolso__date__lte=ultimo_dia_mes_anterior
        ).count()

        # Créditos activos al final del mes
        creditos_activos = Credito.objects.filter(
            estado__in=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA],
            fecha_desembolso__date__lte=ultimo_dia_mes_anterior
        ).count()

        # Créditos en mora al final del mes
        creditos_mora = Credito.objects.filter(
            estado=Credito.EstadoCredito.EN_MORA,
            fecha_desembolso__date__lte=ultimo_dia_mes_anterior
        ).count()

        reporte = {
            'mes': f"{primer_dia_mes_anterior.strftime('%B %Y')}",
            'creditos_desembolsados': creditos_desembolsados,
            'creditos_activos': creditos_activos,
            'creditos_mora': creditos_mora,
            'tasa_mora': f"{(creditos_mora / creditos_activos * 100):.2f}%" if creditos_activos > 0 else "0%"
        }

        logger.info(f"Reporte mensual generado: {reporte}")

        # Aquí podrías enviar el reporte por email a los administradores
        # enviar_email_simple('admin@aprobado.com', 'Reporte Mensual', str(reporte))

        return {
            'status': 'success',
            'reporte': reporte,
            'timestamp': timezone.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error en tarea generar_reporte_cartera_mensual: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': timezone.now().isoformat()
        }

