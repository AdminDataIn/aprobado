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


ETIQUETAS_ESTADO_PUBLICO = {
    'FORMALIZACION_OPERATIVA_FINAL': 'Formalización operativa',
    'FIRMA_CONFIRMADA': 'Firma confirmada',
    'PENDIENTE_FIRMA': 'Firma pendiente',
    'IDENTIDAD_PENDIENTE': 'Validación de identidad',
    'ORIGINADA_EN_REVISION': 'Formalización en curso',
    'APROBADA_PARA_ORIGINAR': 'Validaciones superadas',
    'DEVUELTA_A_REVISION': 'Validación adicional',
    'PENDIENTE_APROBACION_INTERNA': 'Validaciones finales',
    'EVALUACION_PENDIENTE': 'Evaluación pendiente',
    'EN_EVALUACION': 'En evaluación',
    'SUBSANACION_PENDIENTE': 'Información por corregir',
    'VALIDACION_EMPRESA': 'Validación contractual',
    'ERROR_CONTROLADO': 'Revisión en curso',
    'EN_REVISION_MANUAL': 'Revisión en curso',
    'PREAPROBADO_READ_ONLY': 'Evaluación inicial favorable',
    'BLOQUEADO_READ_ONLY': 'Revisión requerida',
    'NO_EVALUABLE': 'Revisión requerida',
    'DOCUMENTOS_PENDIENTES': 'Documentación pendiente',
    'DOCUMENTOS_CARGADOS': 'Documentación completa',
    'ESTADO_OPERATIVO': 'Solicitud registrada',
}

ACCIONES_ESTADO_PUBLICO = {
    'DOCUMENTOS_PENDIENTES': ('DOCUMENTOS', 'Completar documentos'),
    'DOCUMENTOS_CARGADOS': ('SIMULACION', 'Continuar simulación'),
    'SUBSANACION_PENDIENTE': ('SUBSANACION', 'Corregir información'),
    'EVALUACION_PENDIENTE': ('CONDICIONES', 'Ver detalle de la solicitud'),
    'EN_EVALUACION': ('CONDICIONES', 'Ver detalle de la solicitud'),
    'PREAPROBADO_READ_ONLY': ('CONDICIONES', 'Ver condiciones solicitadas'),
    'BLOQUEADO_READ_ONLY': ('DOCUMENTOS', 'Revisar documentos'),
    'NO_EVALUABLE': ('DOCUMENTOS', 'Revisar documentos'),
    'ERROR_CONTROLADO': ('DOCUMENTOS', 'Revisar documentos'),
    'EN_REVISION_MANUAL': ('DOCUMENTOS', 'Revisar documentos'),
    'DEVUELTA_A_REVISION': ('DOCUMENTOS', 'Revisar documentos'),
}

TIPOS_REQUERIMIENTO_POR_DOCUMENTO = {
    'CEDULA_FRONTAL': {RequerimientoSubsanacionPrestador.Tipo.DOCUMENTO_IDENTIDAD},
    'CEDULA_TRASERA': {RequerimientoSubsanacionPrestador.Tipo.DOCUMENTO_IDENTIDAD},
    'CERTIFICADO_BANCARIO': {
        RequerimientoSubsanacionPrestador.Tipo.CERTIFICACION_BANCARIA,
    },
    'CONTRATO': {
        RequerimientoSubsanacionPrestador.Tipo.NUEVO_CONTRATO,
        RequerimientoSubsanacionPrestador.Tipo.ACTUALIZAR_CONTRATO,
        RequerimientoSubsanacionPrestador.Tipo.DOCUMENTO_CONTRACTUAL,
    },
}

DESCRIPCIONES_DOCUMENTO_PUBLICO = {
    'CEDULA_FRONTAL': 'Cara frontal de tu documento de identidad.',
    'CEDULA_TRASERA': 'Cara posterior de tu documento de identidad.',
    'CERTIFICADO_BANCARIO': 'Certificación de la cuenta registrada para el proceso.',
    'CONTRATO': 'Contrato vigente aportado para validar tu información contractual.',
}

NOMBRES_ARCHIVO_PUBLICO = {
    'CEDULA_FRONTAL': 'cedula-frontal',
    'CEDULA_TRASERA': 'cedula-trasera',
    'CERTIFICADO_BANCARIO': 'certificado-bancario',
    'CONTRATO': 'contrato-vigente',
}


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
    accion, accion_etiqueta = ACCIONES_ESTADO_PUBLICO.get(codigo, (None, ''))
    return {
        'codigo': codigo,
        'etiqueta': ETIQUETAS_ESTADO_PUBLICO.get(codigo, 'Solicitud en proceso'),
        'titulo': titulo,
        'detalle': detalle,
        'tono': tono,
        'requerimiento': requerimiento,
        'accion': accion,
        'accion_etiqueta': accion_etiqueta,
    }


def construir_presentacion_documentos(solicitud, tipos_obligatorios):
    documentos = {documento.tipo_documento: documento for documento in solicitud.documentos.all()}
    etiquetas = dict(solicitud.documentos.model.TipoDocumento.choices)
    requerimientos = list(
        solicitud.requerimientos_subsanacion.order_by('-creado_en', '-id')
    )
    presentacion = []

    for tipo in tipos_obligatorios:
        documento = documentos.get(tipo)
        tipos_requerimiento = TIPOS_REQUERIMIENTO_POR_DOCUMENTO.get(str(tipo), set())
        requerimientos_tipo = [
            requerimiento
            for requerimiento in requerimientos
            if requerimiento.tipo in tipos_requerimiento
        ]
        pendiente = next(
            (
                requerimiento
                for requerimiento in requerimientos_tipo
                if requerimiento.estado == RequerimientoSubsanacionPrestador.Estado.PENDIENTE
            ),
            None,
        )
        validado = any(
            requerimiento.estado == RequerimientoSubsanacionPrestador.Estado.VALIDADO
            for requerimiento in requerimientos_tipo
        )

        if pendiente:
            estado = 'CORRECCION'
            estado_etiqueta = 'Requiere corrección'
            estado_detalle = pendiente.mensaje_publico
        elif documento and validado:
            estado = 'VALIDADO'
            estado_etiqueta = 'Validado'
            estado_detalle = 'El documento corregido fue validado por nuestro equipo.'
        elif documento and _documento_fue_reemplazado(documento):
            estado = 'REEMPLAZADO'
            estado_etiqueta = 'Reemplazado'
            estado_detalle = 'Recibimos la versión más reciente de este documento.'
        elif documento:
            estado = 'CARGADO'
            estado_etiqueta = 'Cargado'
            estado_detalle = 'El documento fue recibido correctamente.'
        else:
            estado = 'PENDIENTE'
            estado_etiqueta = 'Pendiente'
            estado_detalle = 'Carga este documento para continuar con tu solicitud.'

        es_identidad = str(tipo) in {'CEDULA_FRONTAL', 'CEDULA_TRASERA'}
        presentacion.append(
            {
                'tipo': tipo,
                'etiqueta': etiquetas.get(tipo, 'Documento requerido'),
                'descripcion': DESCRIPCIONES_DOCUMENTO_PUBLICO.get(
                    str(tipo),
                    'Documento aportado para completar tu solicitud.',
                ),
                'documento': documento,
                'estado': estado,
                'estado_etiqueta': estado_etiqueta,
                'estado_detalle': estado_detalle,
                'icono': 'ID' if es_identidad else 'PDF',
                'accept': 'image/jpeg,image/png' if es_identidad else 'application/pdf',
                'formato': 'JPG o PNG' if es_identidad else 'PDF',
            }
        )
    return presentacion


def construir_detalle_documento_publico(documento):
    tipo = str(documento.tipo_documento)
    return {
        'etiqueta': documento.get_tipo_documento_display(),
        'descripcion': DESCRIPCIONES_DOCUMENTO_PUBLICO.get(
            tipo,
            'Documento aportado para completar tu solicitud.',
        ),
        'nombre_descarga': NOMBRES_ARCHIVO_PUBLICO.get(tipo, 'documento-solicitud'),
        'formato': 'Imagen' if tipo in {'CEDULA_FRONTAL', 'CEDULA_TRASERA'} else 'PDF',
    }


def construir_condiciones_guardadas(solicitud):
    disponible = bool(
        solicitud.monto_simulado
        and solicitud.plazo_simulado_meses
        and solicitud.tasa_mensual_simulacion is not None
        and solicitud.version_configuracion_financiera_simulacion
        and solicitud.simulada_en
    )
    return {
        'disponible': disponible,
        'monto': solicitud.monto_simulado,
        'plazo_meses': solicitud.plazo_simulado_meses,
        'tasa_mensual_porcentaje': solicitud.tasa_mensual_simulacion,
        'version_configuracion': solicitud.version_configuracion_financiera_simulacion,
        'version_politica': solicitud.version_politica_simulacion,
        'monto_maximo_configuracion': solicitud.monto_maximo_configuracion_simulacion,
        'plazo_maximo_configuracion': solicitud.plazo_maximo_configuracion_simulacion,
        'fecha': solicitud.simulada_en,
        'cuota_guardada': None,
        'cargos_guardados': None,
    }


def construir_timeline_publico_solicitud(solicitud, progreso_documental, estado_publico):
    documentos = list(solicitud.documentos.all())
    fecha_documentos = max(
        (documento.updated_at for documento in documentos),
        default=None,
    )
    simulacion_completa = bool(solicitud.monto_solicitado and solicitud.plazo_meses)
    documentos_completos = progreso_documental['completo']

    eventos = [
        {
            'titulo': 'Solicitud registrada',
            'detalle': 'Recibimos tus datos personales y contractuales.',
            'estado': 'COMPLETADO',
            'fecha': solicitud.created_at,
        },
        {
            'titulo': 'Documentación',
            'detalle': (
                'Recibimos los cuatro documentos requeridos.'
                if documentos_completos
                else f"Has cargado {progreso_documental['cargados']} de {progreso_documental['total']} documentos."
            ),
            'estado': 'COMPLETADO' if documentos_completos else 'ACTUAL',
            'fecha': fecha_documentos,
        },
        {
            'titulo': 'Condiciones solicitadas',
            'detalle': (
                'Registramos el monto y plazo seleccionados.'
                if simulacion_completa
                else 'Selecciona el monto y plazo para continuar.'
            ),
            'estado': (
                'COMPLETADO'
                if simulacion_completa
                else ('ACTUAL' if documentos_completos else 'PENDIENTE')
            ),
            'fecha': solicitud.simulada_en,
        },
        {
            'titulo': estado_publico['etiqueta'],
            'detalle': estado_publico['detalle'],
            'estado': 'ACTUAL' if simulacion_completa else 'PENDIENTE',
            'fecha': solicitud.updated_at if simulacion_completa else None,
        },
    ]
    etapa_actual = 2 if not documentos_completos else (3 if not simulacion_completa else 4)
    return {
        'eventos': eventos,
        'etapa_actual': etapa_actual,
        'total_etapas': 4,
    }


def _documento_fue_reemplazado(documento):
    diferencia = documento.updated_at - documento.created_at
    return diferencia.total_seconds() >= 1
