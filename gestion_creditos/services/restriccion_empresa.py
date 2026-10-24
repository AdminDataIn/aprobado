"""Administrative restrictions for NEW applications, never payment or collection rules."""
import json

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from gestion_creditos.models import Empresa, RestriccionEmpresa

PERMISO = 'gestion_creditos.manage_company_restrictions'
MENSAJE_PUBLICO = ('Por ahora no podemos recibir nuevas solicitudes asociadas a esta empresa. '
                   'Puedes consultar con tu empresa o contactar a Aprobado para recibir orientacion.')


class EmpresaRestringida(ValidationError):
    pass


def exigir_gestor(actor):
    if (not actor or not actor.is_authenticated or not actor.is_active or not actor.is_staff
            or hasattr(actor, 'perfil_pagador') or not actor.has_perm(PERMISO)):
        raise PermissionDenied('Se requiere un gestor autorizado sin perfil pagador.')


def restriccion_vigente(empresa_id, producto, *, creada_en=None, ahora=None):
    if producto not in RestriccionEmpresa.Producto.values:
        raise ValidationError('Producto de restriccion no soportado.')
    ahora = ahora or timezone.now()
    qs = RestriccionEmpresa.objects.filter(empresa_id=empresa_id, producto=producto,
        inicio__lte=ahora, levantada_en__isnull=True).filter(Q(vence_en__isnull=True) | Q(vence_en__gt=ahora))
    if creada_en is not None:
        # Previously registered applications are not retroactively restricted.
        qs = qs.filter(creada_en__lte=creada_en, inicio__lte=creada_en)
    return qs.order_by('-creada_en', '-pk').first()


def validar_nueva_solicitud(empresa_id, producto, *, creada_en=None):
    restriccion = restriccion_vigente(empresa_id, producto, creada_en=creada_en)
    if restriccion:
        raise EmpresaRestringida(restriccion.mensaje_publico, code='empresa_restringida')


def _auditar(registro, actor, accion):
    LogEntry.objects.log_action(user_id=actor.pk,
        content_type_id=ContentType.objects.get_for_model(RestriccionEmpresa).pk,
        object_id=registro.pk, object_repr=str(registro), action_flag=accion,
        change_message=json.dumps({'operacion': 'CREAR' if accion == ADDITION else 'LEVANTAR',
            'empresa_id': registro.empresa_id, 'producto': registro.producto,
            'restriccion_id': registro.pk}))


@transaction.atomic
def crear_restriccion(*, empresa_id, producto, actor, motivo_interno, inicio=None,
                      vence_en=None, mensaje_publico=MENSAJE_PUBLICO, umbral_mora_dias=90):
    exigir_gestor(actor)
    Empresa.objects.select_for_update().get(pk=empresa_id)
    inicio = inicio or timezone.now()
    if timezone.is_naive(inicio) or (vence_en and timezone.is_naive(vence_en)):
        raise ValidationError('Las fechas deben incluir zona horaria.')
    if RestriccionEmpresa.objects.filter(empresa_id=empresa_id, producto=producto,
            levantada_en__isnull=True).filter(Q(vence_en__isnull=True) | Q(vence_en__gt=timezone.now())).exists():
        raise ValidationError('Ya existe una restriccion no levantada para este producto.')
    registro = RestriccionEmpresa(empresa_id=empresa_id, producto=producto,
        motivo_interno=str(motivo_interno).strip(), mensaje_publico=str(mensaje_publico).strip(),
        inicio=inicio, vence_en=vence_en, creada_por=actor, umbral_mora_dias=umbral_mora_dias)
    registro.full_clean()
    registro._gestion_por_servicio = True
    registro.save()
    _auditar(registro, actor, ADDITION)
    return registro


@transaction.atomic
def levantar_restriccion(*, restriccion_id, actor, motivo):
    exigir_gestor(actor)
    if not str(motivo or '').strip():
        raise ValidationError('El levantamiento requiere motivo.')
    empresa_id = RestriccionEmpresa.objects.values_list('empresa_id', flat=True).get(pk=restriccion_id)
    Empresa.objects.select_for_update().get(pk=empresa_id)
    registro = RestriccionEmpresa.objects.select_for_update().get(pk=restriccion_id)
    if registro.levantada_en:
        return registro
    registro.levantada_en = timezone.now()
    registro.levantada_por = actor
    registro.motivo_levantamiento = str(motivo).strip()
    registro._gestion_por_servicio = True
    registro.save(update_fields=['levantada_en', 'levantada_por', 'motivo_levantamiento'])
    _auditar(registro, actor, CHANGE)
    return registro
