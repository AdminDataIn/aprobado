from contractors.models import (
    AprobacionInternaPrestador,
    ContractorApplication,
    FormalizacionCreditoPrestador,
    NovedadOperativaPrestador,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
)
from gestion_creditos.models import OrigenCreditoPrestador


def construir_estado_publico_solicitud(solicitud):
    auditoria = solicitud.auditorias_predecision.order_by('-created_at', '-id').first()
    aprobacion = solicitud.aprobaciones_internas.order_by('-creada_en', '-id').first()
    requerimiento = solicitud.requerimientos_subsanacion.filter(
        estado=RequerimientoSubsanacionPrestador.Estado.PENDIENTE
    ).order_by('-creado_en', '-id').first()
    validacion_empresa = solicitud.revisiones_manuales.filter(
        estado=RevisionManualPrestador.Estado.PENDIENTE_VALIDACION_EMPRESA
    ).exists()

    if aprobacion:
        if aprobacion.estado == AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR:
            originada = OrigenCreditoPrestador.objects.filter(
                gate_id=aprobacion.id,
                estado=OrigenCreditoPrestador.Estado.COMPLETADO,
            ).exists()
            if originada:
                origen = OrigenCreditoPrestador.objects.filter(
                    gate_id=aprobacion.id,
                    estado=OrigenCreditoPrestador.Estado.COMPLETADO,
                ).first()
                formalizacion = FormalizacionCreditoPrestador.objects.filter(
                    origen_credito_prestador=origen
                ).first()
                if formalizacion:
                    if formalizacion.estado == FormalizacionCreditoPrestador.Estado.FIRMADO:
                        novedad = NovedadOperativaPrestador.objects.filter(
                            formalizacion=formalizacion
                        ).first()
                        if novedad and novedad.estado in {
                            NovedadOperativaPrestador.Estado.ENVIADA,
                            NovedadOperativaPrestador.Estado.RECIBIDA,
                            NovedadOperativaPrestador.Estado.GESTIONADA,
                        }:
                            return _estado(
                                'FORMALIZACION_OPERATIVA_FINAL',
                                'Tu solicitud esta avanzando en la etapa final de formalizacion operativa.',
                                'Te informaremos cuando finalicen las validaciones operativas.',
                                tono='favorable',
                            )
                        return _estado(
                            'FIRMA_CONFIRMADA',
                            'Recibimos correctamente tu firma y estamos completando las validaciones operativas finales.',
                            'Tu credito permanece en formalizacion y aun no ha sido transferido.',
                            tono='favorable',
                        )
                    if formalizacion.estado == FormalizacionCreditoPrestador.Estado.PENDIENTE_FIRMA:
                        return _estado(
                            'PENDIENTE_FIRMA',
                            'Tu documento esta listo para firma.',
                            'Revisa el canal seguro enviado por el proveedor de firma.',
                            tono='favorable',
                        )
                    if formalizacion.estado in {
                        FormalizacionCreditoPrestador.Estado.PENDIENTE_VALIDACION_IDENTIDAD,
                        FormalizacionCreditoPrestador.Estado.IDENTIDAD_VALIDADA,
                    }:
                        return _estado(
                            'IDENTIDAD_PENDIENTE',
                            'Necesitamos validar tu identidad antes de continuar con la firma.',
                            'Te informaremos cuando el documento este listo para firmar.',
                        )
                return _estado(
                    'ORIGINADA_EN_REVISION',
                    'Tu solicitud está avanzando a la etapa de formalización.',
                    'La obligación permanece en revisión y todavía no ha sido desembolsada.',
                    tono='favorable',
                )
            return _estado(
                'APROBADA_PARA_ORIGINAR',
                'Tu solicitud super\u00f3 las validaciones internas.',
                'Est\u00e1 avanzando a la etapa de formalizaci\u00f3n.',
                tono='favorable',
            )
        if aprobacion.estado == AprobacionInternaPrestador.Estado.DEVUELTA_A_REVISION:
            return _estado(
                'DEVUELTA_A_REVISION',
                'Necesitamos realizar una validaci\u00f3n adicional antes de continuar.',
                'Nuestro equipo revisar\u00e1 nuevamente la informaci\u00f3n registrada.',
                tono='advertencia',
            )
        if aprobacion.estado in {
            AprobacionInternaPrestador.Estado.PENDIENTE,
            AprobacionInternaPrestador.Estado.EN_ANALISIS,
        }:
            return _estado(
                'PENDIENTE_APROBACION_INTERNA',
                'Tu evaluaci\u00f3n inicial fue favorable.',
                'Estamos realizando las validaciones finales.',
                tono='favorable',
            )

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
                'Estamos realizando las validaciones finales.',
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
