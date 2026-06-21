from decimal import Decimal

from django.apps import apps

from gestion_creditos.models import Credito, CreditoAdelantoNomina, CreditoLibranza, VinculoLaboralEmpresa
from gestion_creditos.services.adelanto_nomina_service import evaluar_elegibilidad_adelanto


COLLABORATOR_STATUS_COMPLETO = 'completo'
COLLABORATOR_STATUS_PENDIENTE_INFO = 'pendiente_info'
COLLABORATOR_STATUS_PENDIENTE_VALIDACION = 'pendiente_validacion'
COLLABORATOR_STATUS_CON_CREDITO_ACTIVO = 'con_credito_activo'
COLLABORATOR_STATUS_PENDIENTE_VINCULO = 'pendiente_vinculo'

COLLABORATOR_STATUS_CHOICES = (
    (COLLABORATOR_STATUS_COMPLETO, 'Completo'),
    (COLLABORATOR_STATUS_PENDIENTE_INFO, 'Pendiente información'),
    (COLLABORATOR_STATUS_PENDIENTE_VALIDACION, 'Pendiente validación'),
    (COLLABORATOR_STATUS_CON_CREDITO_ACTIVO, 'Con crédito activo'),
    (COLLABORATOR_STATUS_PENDIENTE_VINCULO, 'Pendiente vínculo laboral'),
)

ACTIVE_CREDIT_STATES = {
    Credito.EstadoCredito.ACTIVO,
    Credito.EstadoCredito.EN_MORA,
}

REQUEST_STATES = {
    Credito.EstadoCredito.SOLICITUD,
    Credito.EstadoCredito.EN_REVISION,
    Credito.EstadoCredito.APROBADO_PAGADOR,
    Credito.EstadoCredito.PENDIENTE_FIRMA,
    Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
}


def _normalize_document(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def _normalize_text(value):
    return str(value or '').strip()


def mask_document(value):
    document = _normalize_document(value)
    if not document:
        return ''
    if len(document) <= 4:
        return '*' * len(document)
    return f"{'*' * (len(document) - 4)}{document[-4:]}"


def _employee_missing_fields(vinculo):
    missing = []
    checks = (
        ('Nombre', vinculo.nombre_empleado),
        ('Documento', vinculo.documento_empleado),
        ('Correo', vinculo.correo_empleado or getattr(vinculo.usuario, 'email', '')),
        ('Fecha alta', vinculo.fecha_alta_aprobado),
        ('Salario base', vinculo.salario_base_mensual),
    )
    for label, value in checks:
        if value in (None, '', Decimal('0.00')):
            missing.append(label)
    return missing


def _documentation_pending(detalle):
    if not detalle:
        return []
    pending = []
    checks = (
        ('Cédula frontal', getattr(detalle, 'cedula_frontal', None)),
        ('Cédula reverso', getattr(detalle, 'cedula_trasera', None)),
        ('Contrato / soporte laboral', getattr(detalle, 'certificado_laboral', None)),
        ('Certificado bancario', getattr(detalle, 'certificado_bancario', None)),
    )
    for label, file_field in checks:
        if not file_field or not getattr(file_field, 'name', ''):
            pending.append(label)
    return pending


def _new_row(key, *, user=None, documento='', nombre='', correo=''):
    return {
        'key': key,
        'usuario': user,
        'vinculo': None,
        'nombre': _normalize_text(nombre),
        'documento': _normalize_document(documento),
        'correo': _normalize_text(correo).lower(),
        'origenes': set(),
        'creditos_libranza': [],
        'creditos_adelanto': [],
        'solicitudes_prestador': [],
        'credito_principal': None,
        'estado_solicitud': '-',
        'tiene_credito_activo': False,
        'tiene_mora': False,
        'estado_credito': '-',
        'empresa_nombre': '',
        'ultima_actualizacion': None,
        'capacidad_pendiente': True,
        'documentacion_pendiente': [],
        'elegibilidad_adelanto': 'Pendiente completar vínculo laboral',
        'adelanto_apto': False,
        'monto_maximo': Decimal('0.00'),
        'missing_fields': ['Vínculo laboral'],
        'status': COLLABORATOR_STATUS_PENDIENTE_VINCULO,
        'status_label': dict(COLLABORATOR_STATUS_CHOICES)[COLLABORATOR_STATUS_PENDIENTE_VINCULO],
        'acciones': {
            'ver_detalle': False,
            'editar_vinculo': False,
            'ver_credito': False,
            'ver_solicitudes': False,
            'ver_documentacion': False,
        },
    }


def _touch_row(row, value):
    if value and (not row['ultima_actualizacion'] or value > row['ultima_actualizacion']):
        row['ultima_actualizacion'] = value


def _row_key(*, user=None, documento=''):
    normalized_document = _normalize_document(documento)
    if normalized_document:
        return f'doc:{normalized_document}'
    if user and getattr(user, 'id', None):
        return f'user:{user.id}'
    return ''


def _get_or_create_row(rows, *, user=None, documento='', nombre='', correo=''):
    key = _row_key(user=user, documento=documento)
    if user and getattr(user, 'id', None):
        for existing in rows.values():
            existing_user = existing.get('usuario')
            if existing_user and existing_user.id == user.id:
                row = existing
                break
        else:
            row = None
        if row:
            if nombre and not row['nombre']:
                row['nombre'] = _normalize_text(nombre)
            if documento and not row['documento']:
                row['documento'] = _normalize_document(documento)
            if correo and not row['correo']:
                row['correo'] = _normalize_text(correo).lower()
            return row
    if not key:
        key = f'tmp:{len(rows) + 1}'
    if key not in rows:
        rows[key] = _new_row(key, user=user, documento=documento, nombre=nombre, correo=correo)
    row = rows[key]
    if user and not row['usuario']:
        row['usuario'] = user
    if nombre and not row['nombre']:
        row['nombre'] = _normalize_text(nombre)
    if documento and not row['documento']:
        row['documento'] = _normalize_document(documento)
    if correo and not row['correo']:
        row['correo'] = _normalize_text(correo).lower()
    return row


def _credit_sort_key(credito):
    return credito.fecha_solicitud or credito.id


def _apply_vinculo(row, vinculo):
    row['vinculo'] = vinculo
    row['usuario'] = vinculo.usuario
    row['nombre'] = vinculo.nombre_empleado or row['nombre']
    row['documento'] = _normalize_document(vinculo.documento_empleado) or row['documento']
    row['correo'] = (vinculo.correo_empleado or getattr(vinculo.usuario, 'email', '') or row['correo']).lower()
    row['empresa_nombre'] = vinculo.empresa.nombre
    row['origenes'].add('vinculo_laboral')
    row['acciones']['ver_detalle'] = True
    row['acciones']['editar_vinculo'] = True
    _touch_row(row, getattr(vinculo, 'actualizado_en', None) or getattr(vinculo, 'creado_en', None))


def _apply_libranza(row, detalle):
    credito = detalle.credito
    row['origenes'].add('solicitud_libranza')
    if credito.estado in ACTIVE_CREDIT_STATES:
        row['origenes'].add('credito_activo')
    row['creditos_libranza'].append(credito)
    row['usuario'] = row['usuario'] or credito.usuario
    row['nombre'] = row['nombre'] or detalle.nombre_completo
    row['documento'] = row['documento'] or _normalize_document(detalle.cedula)
    row['correo'] = row['correo'] or (detalle.correo_electronico or getattr(credito.usuario, 'email', '') or '').lower()
    row['empresa_nombre'] = detalle.empresa.nombre
    row['acciones']['ver_detalle'] = True
    row['acciones']['ver_credito'] = True
    row['acciones']['ver_solicitudes'] = True
    row['acciones']['ver_documentacion'] = True
    _touch_row(row, getattr(credito, 'fecha_solicitud', None))


def _apply_adelanto(row, detalle):
    credito = detalle.credito
    row['origenes'].add('adelanto_nomina')
    if credito.estado in ACTIVE_CREDIT_STATES:
        row['origenes'].add('credito_activo')
    row['creditos_adelanto'].append(credito)
    row['empresa_nombre'] = detalle.vinculo_laboral.empresa.nombre
    row['acciones']['ver_detalle'] = True
    row['acciones']['ver_credito'] = True
    _touch_row(row, getattr(credito, 'fecha_solicitud', None))


def _apply_prestador(row, solicitud, datos_laborales):
    row['origenes'].add('solicitud_prestador')
    row['solicitudes_prestador'].append(solicitud)
    row['usuario'] = row['usuario'] or getattr(solicitud, 'usuario', None)
    row['nombre'] = row['nombre'] or f"{solicitud.first_name} {solicitud.last_name}".strip()
    row['documento'] = row['documento'] or _normalize_document(solicitud.document_number)
    row['correo'] = row['correo'] or (solicitud.email or '').lower()
    row['empresa_nombre'] = datos_laborales.empresa.nombre
    row['estado_solicitud'] = solicitud.get_status_display()
    row['acciones']['ver_detalle'] = True
    row['acciones']['ver_solicitudes'] = True
    _touch_row(row, getattr(solicitud, 'updated_at', None) or getattr(solicitud, 'created_at', None))


def _finalize_row(row):
    creditos = sorted(row['creditos_libranza'] + row['creditos_adelanto'], key=_credit_sort_key, reverse=True)
    active_credit = next((credito for credito in creditos if credito.estado in ACTIVE_CREDIT_STATES), None)
    mora_credit = next((credito for credito in creditos if credito.estado == Credito.EstadoCredito.EN_MORA), None)
    row['credito_principal'] = active_credit or (creditos[0] if creditos else None)
    row['tiene_credito_activo'] = bool(active_credit)
    row['tiene_mora'] = bool(mora_credit)
    if row['credito_principal']:
        row['estado_solicitud'] = row['credito_principal'].get_estado_display()
        row['estado_credito'] = active_credit.get_estado_display() if active_credit else row['credito_principal'].get_estado_display()
    elif row['solicitudes_prestador']:
        row['estado_solicitud'] = row['solicitudes_prestador'][0].get_status_display()

    pending_docs = []
    for credito in row['creditos_libranza']:
        pending_docs.extend(_documentation_pending(getattr(credito, 'detalle_libranza', None)))
    seen_docs = set()
    row['documentacion_pendiente'] = [
        item for item in pending_docs
        if not (item in seen_docs or seen_docs.add(item))
    ]

    vinculo = row['vinculo']
    if not vinculo:
        row['missing_fields'] = ['Vínculo laboral']
        row['capacidad_pendiente'] = True
        row['elegibilidad_adelanto'] = 'Pendiente completar vínculo laboral'
        row['status'] = COLLABORATOR_STATUS_PENDIENTE_VINCULO
    else:
        missing = _employee_missing_fields(vinculo)
        row['missing_fields'] = missing
        row['capacidad_pendiente'] = bool(missing)
        eligibility = evaluar_elegibilidad_adelanto(vinculo.usuario)
        row['adelanto_apto'] = bool(
            eligibility.get('eligible')
            and eligibility.get('vinculo')
            and eligibility['vinculo'].id == vinculo.id
        )
        row['monto_maximo'] = eligibility.get('monto_maximo') or Decimal('0.00')
        row['elegibilidad_adelanto'] = (
            'Apto para adelanto'
            if row['adelanto_apto']
            else (eligibility.get('reason') or 'No elegible para adelanto')
        )
        if missing:
            row['status'] = COLLABORATOR_STATUS_PENDIENTE_INFO
        elif not vinculo.validado_por_pagador:
            row['status'] = COLLABORATOR_STATUS_PENDIENTE_VALIDACION
        else:
            row['status'] = COLLABORATOR_STATUS_COMPLETO

    if row['tiene_credito_activo'] and row['status'] == COLLABORATOR_STATUS_COMPLETO:
        row['status'] = COLLABORATOR_STATUS_CON_CREDITO_ACTIVO

    row['status_label'] = dict(COLLABORATOR_STATUS_CHOICES).get(row['status'], row['status'])
    row['origenes'] = sorted(row['origenes'])
    row['documento_enmascarado'] = mask_document(row['documento'])
    return row


def _matches_search(row, search):
    if not search:
        return True
    haystack = ' '.join([
        row.get('nombre', ''),
        row.get('documento', ''),
        row.get('correo', ''),
    ]).lower()
    return search.lower() in haystack


def _matches_status(row, status_filter):
    if not status_filter:
        return True
    if status_filter == COLLABORATOR_STATUS_CON_CREDITO_ACTIVO:
        return row['tiene_credito_activo']
    return row['status'] == status_filter


def _matches_active_credit(row, active_filter):
    if not active_filter:
        return True
    expected = active_filter in {'1', 'true', 'True', 'on', 'si'}
    return row['tiene_credito_activo'] is expected


def _matches_pending_link(row, pending_filter):
    if not pending_filter:
        return True
    expected = pending_filter in {'1', 'true', 'True', 'on', 'si'}
    return (row['status'] == COLLABORATOR_STATUS_PENDIENTE_VINCULO) is expected


def _iter_solicitudes_prestador_empresa(empresa):
    try:
        InformacionLaboral = apps.get_model('contractors', 'InformacionLaboralSolicitudContratista')
    except LookupError:
        return []
    return (
        InformacionLaboral.objects
        .select_related('solicitud', 'solicitud__usuario', 'empresa')
        .filter(empresa=empresa)
        .order_by('-solicitud__updated_at', '-solicitud__created_at')
    )


def build_pagador_collaborators_context(
    empresa,
    search='',
    estado_filter='',
    con_credito_activo='',
    pendiente_vinculo='',
):
    rows = {}

    vinculos = (
        VinculoLaboralEmpresa.objects
        .select_related('usuario', 'empresa')
        .filter(empresa=empresa)
        .order_by('nombre_empleado', 'documento_empleado')
    )
    for vinculo in vinculos:
        row = _get_or_create_row(
            rows,
            user=vinculo.usuario,
            documento=vinculo.documento_empleado,
            nombre=vinculo.nombre_empleado,
            correo=vinculo.correo_empleado,
        )
        _apply_vinculo(row, vinculo)

    detalles_libranza = (
        CreditoLibranza.objects
        .select_related('credito', 'credito__usuario', 'empresa')
        .filter(empresa=empresa)
        .order_by('-credito__fecha_solicitud', '-credito__id')
    )
    for detalle in detalles_libranza:
        row = _get_or_create_row(
            rows,
            user=detalle.credito.usuario,
            documento=detalle.cedula,
            nombre=detalle.nombre_completo,
            correo=detalle.correo_electronico,
        )
        _apply_libranza(row, detalle)

    detalles_adelanto = (
        CreditoAdelantoNomina.objects
        .select_related('credito', 'credito__usuario', 'vinculo_laboral', 'vinculo_laboral__usuario', 'vinculo_laboral__empresa')
        .filter(vinculo_laboral__empresa=empresa)
        .order_by('-credito__fecha_solicitud', '-credito__id')
    )
    for detalle in detalles_adelanto:
        vinculo = detalle.vinculo_laboral
        row = _get_or_create_row(
            rows,
            user=vinculo.usuario,
            documento=vinculo.documento_empleado,
            nombre=vinculo.nombre_empleado,
            correo=vinculo.correo_empleado,
        )
        if not row['vinculo']:
            _apply_vinculo(row, vinculo)
        _apply_adelanto(row, detalle)

    for datos_laborales in _iter_solicitudes_prestador_empresa(empresa):
        solicitud = datos_laborales.solicitud
        row = _get_or_create_row(
            rows,
            user=solicitud.usuario,
            documento=solicitud.document_number,
            nombre=f"{solicitud.first_name} {solicitud.last_name}".strip(),
            correo=solicitud.email,
        )
        _apply_prestador(row, solicitud, datos_laborales)

    colaboradores = [_finalize_row(row) for row in rows.values()]
    colaboradores = [
        row for row in colaboradores
        if (
            _matches_search(row, search)
            and _matches_status(row, estado_filter)
            and _matches_active_credit(row, con_credito_activo)
            and _matches_pending_link(row, pendiente_vinculo)
        )
    ]
    colaboradores.sort(key=lambda row: (row['nombre'] or row['documento'] or '').upper())

    summary = {
        'total': len(colaboradores),
        'completos': sum(1 for row in colaboradores if row['status'] in {COLLABORATOR_STATUS_COMPLETO, COLLABORATOR_STATUS_CON_CREDITO_ACTIVO}),
        'pendientes_info': sum(1 for row in colaboradores if row['status'] == COLLABORATOR_STATUS_PENDIENTE_INFO),
        'pendientes_validacion': sum(1 for row in colaboradores if row['status'] == COLLABORATOR_STATUS_PENDIENTE_VALIDACION),
        'pendientes_vinculo': sum(1 for row in colaboradores if row['status'] == COLLABORATOR_STATUS_PENDIENTE_VINCULO),
        'con_credito_activo': sum(1 for row in colaboradores if row['tiene_credito_activo']),
        'en_solicitud': sum(
            1 for row in colaboradores
            if 'solicitud_libranza' in row['origenes'] or 'solicitud_prestador' in row['origenes']
        ),
        'en_mora': sum(1 for row in colaboradores if row['tiene_mora']),
        'aptos_adelanto': sum(1 for row in colaboradores if row['adelanto_apto']),
    }

    return {
        'empleados_gestion': colaboradores,
        'empleados_summary': summary,
        'empleado_search': search,
        'empleado_estado': estado_filter,
        'empleado_con_credito_activo': con_credito_activo,
        'empleado_pendiente_vinculo': pendiente_vinculo,
        'empleado_status_choices': COLLABORATOR_STATUS_CHOICES,
        'vinculo_estado_choices': VinculoLaboralEmpresa.EstadoVinculo.choices,
    }


def build_pagador_employee_detail_context(empresa, documento):
    documento_normalizado = _normalize_document(documento)
    context = build_pagador_collaborators_context(empresa)
    colaborador = next(
        (row for row in context['empleados_gestion'] if row['documento'] == documento_normalizado),
        None,
    )
    if not colaborador:
        return None
    return {
        'colaborador': colaborador,
        'creditos': sorted(
            colaborador['creditos_libranza'] + colaborador['creditos_adelanto'],
            key=_credit_sort_key,
            reverse=True,
        ),
        'solicitudes_prestador': colaborador['solicitudes_prestador'],
        'vinculo': colaborador['vinculo'],
    }
