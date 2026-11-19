"""Manage the technical actor and publish only the existing recovery task."""
import json
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

from contractors.models import ContractorApplication, EvaluacionPrestadorJob as Job
from contractors.tasks import publicar_evaluaciones_pendientes_task


CUENTA = 'svc_prestadores_evaluacion'
PERFILES = {
    'v1': {'can_evaluate_contractor_application'},
    'v3': {'can_evaluate_contractor_application', 'can_decide_contractor_internal_approval'},
}


def perfil(actor):
    if (not actor or actor.username != CUENTA or not actor.is_active or not actor.is_staff
            or actor.is_superuser or actor.has_usable_password() or actor.groups.exists()
            or hasattr(actor, 'perfil_pagador')):
        return None
    permisos = actor.get_all_permissions()
    return next((p for p, codes in PERFILES.items()
                 if permisos == {'contractors.' + c for c in codes}), None)


def configuracion():
    actor = get_user_model().objects.filter(username=CUENTA).first()
    return {
        'actor_id': actor.pk if actor else None, 'perfil': perfil(actor),
        'actor_configurado': bool(actor and str(actor.pk) == str(settings.PRESTADORES_EVALUACION_ACTOR_ID)),
        'auto': settings.PRESTADORES_AUTO_EVALUACION_ENABLED,
        'fuentes_habilitadas': bool(settings.DATACREDITO_ENABLED and settings.DATACREDITO_REAL_ENABLED),
        'recuperacion': settings.PRESTADORES_RECUPERACION_ENABLED,
        'dispatcher_beat_activo': PeriodicTask.objects.filter(enabled=True, task=publicar_evaluaciones_pendientes_task.name).exists(),
    }


def estado():
    ahora = timezone.now()
    tarde = ahora - timedelta(seconds=120)
    return {
        **configuracion(),
        'jobs': dict(Job.objects.values_list('estado').annotate(n=Count('pk')).order_by('estado')),
        'publicacion_atrasada': Job.objects.filter(estado__in=('PENDIENTE_PUBLICACION', 'ENCOLADA'),
                                                   proxima_publicacion__lt=tarde).count(),
        'lease_vencido': Job.objects.filter(estado='EN_EJECUCION', lease_hasta__lte=ahora).count(),
        'inciertos': Job.objects.filter(estado='REQUIERE_REVISION', codigo_operativo='ejecucion_incierta').count(),
        'pendientes_sin_job': ContractorApplication.objects.activas().filter(
            estado='EVALUACION_PENDIENTE', evaluacion_jobs__isnull=True, simulada_en__lt=tarde).count(),
    }


class Command(BaseCommand):
    help = 'Administra el actor; estado es solo lectura; tick publica solo el dispatcher autorizado.'

    def add_arguments(self, parser):
        parser.add_argument('accion', choices=('estado', 'provisionar', 'retirar', 'tick'))
        parser.add_argument('--perfil', choices=PERFILES, default='v1')
        parser.add_argument('--operador-id', type=int)
        parser.add_argument('--motivo')
        parser.add_argument('--aplicar', action='store_true')

    def handle(self, *args, **o):
        accion = o['accion']
        if accion == 'estado':
            return json.dumps(estado(), sort_keys=True)
        if accion == 'tick':
            if not settings.PRESTADORES_RECUPERACION_ENABLED:
                raise CommandError('Recuperacion no autorizada.')
            if publicar_evaluaciones_pendientes_task.app.conf.task_always_eager:
                raise CommandError('El timer requiere un consumidor separado.')
            e = configuracion()
            if not (e['auto'] and e['fuentes_habilitadas'] and e['actor_configurado'] and e['perfil']) or e['dispatcher_beat_activo']:
                raise CommandError('Configuracion de despacho incompatible.')
            try:
                publicar_evaluaciones_pendientes_task.apply_async(queue='celery', expires=55, retry=False)
            except Exception:
                raise CommandError('Publicacion incierta; revisar broker y siguiente tick.') from None
            return 'DISPATCHER_ENVIADO'
        if not o['aplicar']:
            return 'SIN_ESCRITURA: se requiere autorizacion y --aplicar'
        return self.gestionar_actor(accion, o)

    @transaction.atomic
    def gestionar_actor(self, accion, o):
        User = get_user_model()
        op = User.objects.select_for_update().filter(pk=o['operador_id'], is_active=True,
                                                     is_staff=True, is_superuser=True).first()
        motivo = (o['motivo'] or '').strip()
        if not op or hasattr(op, 'perfil_pagador') or not 1 <= len(motivo) <= 200:
            raise CommandError('Operador administrativo y motivo requeridos.')
        obj = User.objects.select_for_update().filter(username=CUENTA).first()
        nuevo = obj is None
        if nuevo and accion == 'retirar':
            raise CommandError('Cuenta tecnica inexistente.')
        if nuevo:
            obj = User.objects.create_user(username=CUENTA, password=None, is_staff=True)
        else:
            historial = LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(User),
                                                 object_id=str(obj.pk))
            propia = historial.filter(action_flag=ADDITION, change_message__startswith='P0C:provisionar:').exists()
            ultimo = historial.order_by('-pk').values_list('change_message', flat=True).first() or ''
            retirada = (not obj.is_active and not obj.is_staff and not obj.is_superuser
                        and not obj.has_usable_password() and not hasattr(obj, 'perfil_pagador')
                        and not obj.groups.exists() and not obj.user_permissions.exists()
                        and ultimo.startswith('P0C:retirar:'))
            if not propia or not (perfil(obj) or retirada):
                raise CommandError('Cuenta no administrada o permisos inesperados.')
            if retirada and accion == 'retirar':
                return f'ACTOR_SIN_CAMBIO id={obj.pk}'
        if accion == 'retirar':
            obj.user_permissions.clear()
            obj.is_active = obj.is_staff = False
            obj.save(update_fields=['is_active', 'is_staff'])
        else:
            if not nuevo and perfil(obj) == o['perfil']:
                return f'ACTOR_SIN_CAMBIO id={obj.pk}'
            codes = PERFILES[o['perfil']]
            permisos = Permission.objects.filter(content_type__app_label='contractors', codename__in=codes)
            if permisos.count() != len(codes):
                raise CommandError('Catalogo de permisos incompleto.')
            obj.is_active = obj.is_staff = True
            obj.save(update_fields=['is_active', 'is_staff'])
            obj.user_permissions.set(permisos)
            if perfil(User.objects.get(pk=obj.pk)) != o['perfil']:
                raise CommandError('Permisos efectivos no coinciden.')
        flag = ADDITION if nuevo else CHANGE
        LogEntry.objects.create(user=op, content_type=ContentType.objects.get_for_model(obj), object_id=str(obj.pk),
            object_repr=CUENTA, action_flag=flag,
            change_message=f'P0C:{accion}:{o["perfil"]}:{motivo}')
        return f'OPERACION_REGISTRADA accion={accion} id={obj.pk}'
