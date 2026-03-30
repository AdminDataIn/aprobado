from dataclasses import dataclass
from functools import wraps

from django.contrib.auth import get_user_model
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from gestion_creditos.models import Credito
from usuarios.models import ProductAccessProfile


class ProductFlowConflict(Exception):
    pass


@dataclass(frozen=True)
class ProductFlowInfo:
    code: str
    home_path: str
    label: str


FLOW_CONFIG = {
    ProductAccessProfile.ProductFlow.LIBRANZA: ProductFlowInfo(
        code=ProductAccessProfile.ProductFlow.LIBRANZA,
        home_path='/libranza/mi-credito/',
        label='Libranza',
    ),
    ProductAccessProfile.ProductFlow.EMPRENDIMIENTO: ProductFlowInfo(
        code=ProductAccessProfile.ProductFlow.EMPRENDIMIENTO,
        home_path='/emprendimiento/mi-credito/',
        label='Emprendimiento',
    ),
    ProductAccessProfile.ProductFlow.INVERSIONISTA: ProductFlowInfo(
        code=ProductAccessProfile.ProductFlow.INVERSIONISTA,
        home_path='/inversionista/',
        label='Inversionista',
    ),
    ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER: ProductFlowInfo(
        code=ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER,
        home_path='/marketplace/',
        label='Marketplace',
    ),
}


def _infer_flow_from_existing_data(user):
    if hasattr(user, 'perfil_pagador'):
        return ProductAccessProfile.ProductFlow.LIBRANZA

    if hasattr(user, 'perfil_marketing') and user.perfil_marketing.activo:
        return None

    qs = Credito.objects.filter(usuario=user).values_list('linea', flat=True).distinct()
    lineas = set(qs)
    if lineas == {Credito.LineaCredito.LIBRANZA}:
        return ProductAccessProfile.ProductFlow.LIBRANZA
    if lineas == {Credito.LineaCredito.EMPRENDIMIENTO}:
        return ProductAccessProfile.ProductFlow.EMPRENDIMIENTO
    return None


def get_user_flow(user):
    if not getattr(user, 'is_authenticated', False):
        return None

    profile = getattr(user, 'product_access_profile', None)
    if profile:
        return profile.flow

    inferred = _infer_flow_from_existing_data(user)
    if inferred:
        ProductAccessProfile.objects.get_or_create(
            usuario=user,
            defaults={'flow': inferred},
        )
        return inferred
    return None


def assign_user_flow(user, flow):
    if not getattr(user, 'is_authenticated', False):
        return None

    existing_flow = get_user_flow(user)
    if existing_flow and existing_flow != flow:
        raise ProductFlowConflict(existing_flow)

    profile, _created = ProductAccessProfile.objects.get_or_create(
        usuario=user,
        defaults={'flow': flow},
    )
    if profile.flow != flow:
        raise ProductFlowConflict(profile.flow)
    return profile


def is_conflicting_flow(user, target_flow):
    existing_flow = get_user_flow(user)
    return bool(existing_flow and existing_flow != target_flow)


def get_flow_home_path(flow):
    info = FLOW_CONFIG.get(flow)
    return info.home_path if info else '/'


def get_flow_label(flow):
    info = FLOW_CONFIG.get(flow)
    return info.label if info else 'tu producto'


def flow_login_required(target_flow, login_url):
    """
    Exige autenticacion y bloquea el acceso si el usuario ya pertenece
    a otro flujo persistido.
    """
    def decorator(view_func):
        @login_required(login_url=login_url)
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            current_flow = get_user_flow(request.user)
            if current_flow and current_flow != target_flow:
                messages.error(
                    request,
                    f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede usarse aqui.'
                )
                return redirect(get_flow_home_path(current_flow))
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def path_login_required(view_func):
    """
    Usa el prefijo del path para enviar al login correcto y aplicar el
    aislamiento de flujo cuando el usuario ya está autenticado.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        path = request.path or ''
        if path.startswith('/libranza/'):
            target_flow = ProductAccessProfile.ProductFlow.LIBRANZA
            login_url = '/libranza/login/'
        elif path.startswith('/emprendimiento/'):
            target_flow = ProductAccessProfile.ProductFlow.EMPRENDIMIENTO
            login_url = '/emprendimiento/login/'
        elif path.startswith('/marketplace/'):
            target_flow = ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER
            login_url = '/marketplace/login/'
        elif path.startswith('/inversionista/'):
            target_flow = ProductAccessProfile.ProductFlow.INVERSIONISTA
            login_url = '/inversionista/login/'
        else:
            target_flow = None
            login_url = '/auth/login/'

        if not request.user.is_authenticated:
            return redirect(f'{login_url}?next={request.get_full_path()}')

        current_flow = get_user_flow(request.user)
        if target_flow and current_flow and current_flow != target_flow:
            messages.error(
                request,
                f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede usarse aqui.'
            )
            return redirect(get_flow_home_path(current_flow))
        return view_func(request, *args, **kwargs)
    return _wrapped
