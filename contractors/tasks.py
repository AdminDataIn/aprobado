from celery import shared_task


@shared_task(ignore_result=True)
def evaluar_solicitud_prestador_task(job_id):
    from contractors.services.orquestacion_evaluacion import ejecutar_job
    return ejecutar_job(job_id)


@shared_task(ignore_result=True)
def publicar_evaluaciones_pendientes_task():
    """Opt-in dispatcher. Not registered in Beat by default."""
    from contractors.services.orquestacion_evaluacion import despachar_pendientes
    return despachar_pendientes()
