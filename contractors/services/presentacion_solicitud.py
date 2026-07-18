from contractors.models import (
    ContractorApplication,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
)


def construir_estado_publico_solicitud(solicitud):
    auditoria = solicitud.auditorias_predecision.order_by('-created_at', '-id').first()
    requerimiento = solicitud.requerimientos_subsanacion.filter(
        estado=RequerimientoSubsanacionPrestador.Estado.PENDIENTE
    ).order_by('-creado_en', '-id').first()
    validacion_empresa = solicitud.revisiones_manuales.filter(
        estado=RevisionManualPrestador.Estado.PENDIENTE_VALIDACION_EMPRESA
    ).exists()

    if solicitud.estado == ContractorApplication.Estado.EVALUACION_PENDIENTE:
        return _estado(
            'EVALUACION_PENDIENTE',
            'Tu solicitud está pendiente de evaluación.',
            'Validaremos la información y documentos registrados antes de continuar.',
        )
    if solicitud.estado == ContractorApplication.Estado.EN_EVALUACION:
        return _estado(
            'EN_EVALUACION',
            'Estamos evaluando tu solicitud.',
            'Te mostraremos el estado general cuando termine la validación.',
        )
    if solicitud.estado == ContractorApplication.Estado.EN_REVISION_MANUAL:
        if requerimiento:
            return _estado(
                'SUBSANACION_PENDIENTE',
                'Tu solicitud requiere una validación adicional.',
                requerimiento.mensaje_publico,
                requerimiento=requerimiento,
                tono='advertencia',
            )
        if validacion_empresa:
            return _estado(
                'VALIDACION_EMPRESA',
                'Tu solicitud requiere una validación adicional.',
                'Estamos validando información contractual con la empresa.',
                tono='advertencia',
            )
        if auditoria and auditoria.resultado == PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO:
            return _estado(
                'ERROR_CONTROLADO',
                'No fue posible completar la evaluación en este momento.',
                'Tu solicitud será revisada por nuestro equipo.',
                tono='advertencia',
            )
        return _estado(
            'EN_REVISION_MANUAL',
            'Tu solicitud requiere una validación adicional.',
            'Nuestro equipo revisará la información registrada para continuar.',
            tono='advertencia',
        )
    if solicitud.estado == ContractorApplication.Estado.EVALUACION_COMPLETADA and auditoria:
        if auditoria.resultado == PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY:
            return _estado(
                'PREAPROBADO_READ_ONLY',
                'Tu evaluación inicial fue favorable.',
                'Estamos validando los pasos finales antes de formalizar una oferta.',
                tono='favorable',
            )
        if auditoria.resultado == PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY:
            return _estado(
                'BLOQUEADO_READ_ONLY',
                'No podemos continuar automáticamente con esta solicitud.',
                'Puedes revisar la información registrada o comunicarte con nuestro equipo.',
                tono='advertencia',
            )
        if auditoria.resultado in {
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO,
        }:
            return _estado(
                auditoria.resultado,
                'No fue posible completar la evaluación en este momento.',
                'Tu solicitud será revisada por nuestro equipo.',
                tono='advertencia',
            )

    if solicitud.estado == ContractorApplication.Estado.DOCUMENTOS_PENDIENTES:
        return _estado(
            'DOCUMENTOS_PENDIENTES',
            'Completa la documentación de tu solicitud.',
            'Carga los documentos obligatorios para continuar.',
        )
    if solicitud.estado == ContractorApplication.Estado.DOCUMENTOS_CARGADOS:
        return _estado(
            'DOCUMENTOS_CARGADOS',
            'Recibimos tu documentación.',
            'Completa la simulación para registrar las condiciones solicitadas.',
        )
    return _estado(
        'ESTADO_OPERATIVO',
        'Tu solicitud esta registrada.',
        'Consulta aqui los avances del proceso.',
    )


def _estado(codigo, titulo, detalle, *, requerimiento=None, tono='neutral'):
    return {
        'codigo': codigo,
        'titulo': titulo,
        'detalle': detalle,
        'tono': tono,
        'requerimiento': requerimiento,
    }
