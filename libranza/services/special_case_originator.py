from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction

from gestion_creditos.models import Credito, CreditoLibranza, CreditoReglaEspecialAudit, HistorialEstado
from gestion_creditos.services.name_normalization import normalize_name_upper
from gestion_creditos.services.costo_originacion_libranza import crear_snapshot_originacion_libranza
from libranza.services.special_cases import MAX_SPECIAL_CASE_MONTHLY_RATE, TWOPLACES


class SpecialCaseOriginationError(ValueError):
    pass


@dataclass(frozen=True)
class SpecialCaseOriginationResult:
    credito: Credito
    detalle: CreditoLibranza
    audit: CreditoReglaEspecialAudit


@transaction.atomic
def originate_special_case_libranza(*, audit_id, applicant_data, files, originated_by) -> SpecialCaseOriginationResult:
    try:
        audit = CreditoReglaEspecialAudit.objects.select_for_update().get(pk=audit_id)
    except CreditoReglaEspecialAudit.DoesNotExist as exc:
        raise SpecialCaseOriginationError('audit_not_found') from exc

    if audit.credito_id:
        raise SpecialCaseOriginationError('audit_already_originated')

    monthly_rate = _normalize_monthly_rate_for_credit(audit.monthly_rate)
    user = _get_or_create_user(applicant_data)
    credito = Credito.objects.create(
        usuario=user,
        linea=Credito.LineaCredito.LIBRANZA,
        estado=Credito.EstadoCredito.EN_REVISION,
        monto_solicitado=audit.amount,
        plazo_solicitado=audit.term_months,
        monto_aprobado=audit.amount,
        plazo=audit.term_months,
        tasa_interes=monthly_rate,
        comision=audit.commission_amount,
        iva_comision=audit.vat_amount,
        tipo_regla_credito=Credito.TipoReglaCredito.ESPECIAL,
        plazo_forzado=audit.term_months,
        tasa_forzada=monthly_rate,
        observacion_regla_especial=audit.business_reason,
    )

    detalle = CreditoLibranza.objects.create(
        credito=credito,
        nombres=normalize_name_upper(applicant_data['nombres']),
        apellidos=normalize_name_upper(applicant_data['apellidos']),
        cedula=applicant_data['numero_documento'],
        direccion=applicant_data['direccion'],
        telefono=applicant_data['celular'],
        correo_electronico=applicant_data['correo'],
        empresa=applicant_data['empresa'],
        ingresos_mensuales=applicant_data.get('ingresos_mensuales'),
        cedula_frontal=files['cedula_frontal'],
        cedula_trasera=files['cedula_trasera'],
        certificado_laboral=files.get('certificado_laboral'),
        desprendible_nomina=files.get('desprendible_nomina'),
        certificado_bancario=files['certificado_bancario'],
        certificado_bancario_metadata={
            'estado': 'pendiente',
            'origen': 'caso_especial_libranza',
            'audit_id': audit.id,
        },
    )

    audit.credito = credito
    payload = dict(audit.simulation_payload or {})
    payload['origination'] = {
        'originated_by_id': originated_by.id if originated_by else None,
        'tipo_documento': applicant_data.get('tipo_documento'),
        'numero_documento': applicant_data.get('numero_documento'),
        'credito_id': credito.id,
    }
    audit.simulation_payload = payload
    audit.save(update_fields=['credito', 'simulation_payload'])
    crear_snapshot_originacion_libranza(
        credito=credito,
        regla_especial=audit,
    )

    HistorialEstado.objects.create(
        credito=credito,
        estado_anterior=None,
        estado_nuevo=Credito.EstadoCredito.EN_REVISION,
        usuario_modificacion=originated_by,
        motivo=f'Credito especial de libranza originado desde auditoria #{audit.id}. {audit.business_reason}',
    )

    return SpecialCaseOriginationResult(credito=credito, detalle=detalle, audit=audit)


def _normalize_monthly_rate_for_credit(value):
    monthly_rate = Decimal(str(value))
    if monthly_rate < Decimal('0.00'):
        raise SpecialCaseOriginationError('monthly_rate_negative')
    if monthly_rate > MAX_SPECIAL_CASE_MONTHLY_RATE:
        raise SpecialCaseOriginationError('monthly_rate_exceeds_special_case_limit')
    normalized = monthly_rate.quantize(TWOPLACES)
    if monthly_rate != normalized:
        raise SpecialCaseOriginationError('monthly_rate_too_precise')
    return normalized


def _get_or_create_user(applicant_data):
    User = get_user_model()
    email = str(applicant_data['correo']).strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user:
        updates = []
        if not user.first_name:
            user.first_name = normalize_name_upper(applicant_data['nombres'])[:150]
            updates.append('first_name')
        if not user.last_name:
            user.last_name = normalize_name_upper(applicant_data['apellidos'])[:150]
            updates.append('last_name')
        if updates:
            user.save(update_fields=updates)
        return user

    username = email or f"libranza-{applicant_data['numero_documento']}"
    user = User.objects.create(
        username=username,
        email=email,
        first_name=normalize_name_upper(applicant_data['nombres'])[:150],
        last_name=normalize_name_upper(applicant_data['apellidos'])[:150],
        is_active=True,
    )
    user.set_unusable_password()
    user.save(update_fields=['password'])
    return user
