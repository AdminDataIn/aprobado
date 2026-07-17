from contractors.models import TimelinePrestador


CLAVES_METADATA_PERMITIDAS = {
    'auditoria_id',
    'campos',
    'estado_ejecucion',
    'modo_evaluacion',
    'motivo',
    'resultado',
    'snapshot_id',
    'servicio',
    'estado',
    'reutilizado',
    'error_codigo',
    'version_datos',
}


def registrar_evento_timeline_prestador(
    *, solicitud, tipo_evento, titulo, descripcion='', metadata=None,
    visible_cliente=False, usuario=None,
):
    return TimelinePrestador.objects.create(
        solicitud=solicitud,
        tipo_evento=tipo_evento,
        titulo=str(titulo or '')[:160],
        descripcion=str(descripcion or ''),
        metadata=_sanitizar_metadata(metadata or {}),
        visible_cliente=bool(visible_cliente),
        creado_por=usuario if getattr(usuario, 'is_authenticated', False) else None,
    )


def _sanitizar_metadata(metadata):
    if not isinstance(metadata, dict):
        return {}
    resultado = {}
    for clave, valor in metadata.items():
        if clave not in CLAVES_METADATA_PERMITIDAS:
            continue
        if isinstance(valor, (str, int, float, bool)) or valor is None:
            resultado[clave] = valor
        elif isinstance(valor, (list, tuple)):
            resultado[clave] = [
                item for item in valor
                if isinstance(item, (str, int, float, bool)) or item is None
            ]
    return resultado
