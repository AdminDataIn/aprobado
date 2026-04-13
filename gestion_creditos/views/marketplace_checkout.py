import hashlib
import uuid

from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required

from ..decorators import marketplace_buyer_required
from ..forms_marketplace import MarketplaceCheckoutForm
from ..models import MarketplaceItem, MarketplacePedido
from ..services.marketplace_checkout_service import (
    calcular_totales_checkout,
    crear_pedido_marketplace,
    enviar_notificaciones_pedido_marketplace,
    existe_pedido_pendiente_repetido,
)


CHECKOUT_SESSION_KEY = 'marketplace_checkout_context'
CHECKOUT_COOLDOWN_SECONDS = 45
CHECKOUT_IDEMPOTENCY_SECONDS = 600


def _marketplace_user_context(request):
    user = request.user
    is_authenticated = bool(getattr(user, 'is_authenticated', False))
    is_admin = bool(
        is_authenticated
        and hasattr(user, 'perfil_marketing')
        and getattr(user.perfil_marketing, 'activo', False)
    )
    return {
        'marketplace_is_authenticated': is_authenticated,
        'marketplace_is_admin_user': is_admin,
        'marketplace_display_name': (
            user.first_name or user.get_full_name() or user.email
            if is_authenticated else ''
        ),
    }


def _client_ip(request):
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return forwarded or request.META.get('REMOTE_ADDR') or '0.0.0.0'


def _get_checkout_context(request):
    return request.session.get(CHECKOUT_SESSION_KEY, {})


def _set_checkout_context(request, item, token):
    context = _get_checkout_context(request)
    context[str(item.id)] = {
        'token': token,
        'issued_at': timezone.now().isoformat(),
    }
    request.session[CHECKOUT_SESSION_KEY] = context
    request.session.modified = True


def _clear_checkout_context(request, item_id):
    context = _get_checkout_context(request)
    context.pop(str(item_id), None)
    request.session[CHECKOUT_SESSION_KEY] = context
    request.session.modified = True


def _user_can_view_pedido(request, pedido):
    if not getattr(request.user, 'is_authenticated', False):
        return False

    if pedido.comprador_id and pedido.comprador_id == request.user.id:
        return True

    if hasattr(request.user, 'perfil_marketing'):
        perfil_marketing = request.user.perfil_marketing
        if perfil_marketing.activo and perfil_marketing.empresa_id == pedido.empresa_id:
            return True

    return False


@login_required(login_url='/marketplace/login/')
@marketplace_buyer_required
@require_http_methods(["GET", "POST"])
def marketplace_checkout_view(request, item_id):
    item = get_object_or_404(
        MarketplaceItem.objects.select_related('empresa'),
        pk=item_id,
        estado=MarketplaceItem.EstadoItem.APROBADO,
    )

    totals_error = ''
    try:
        totals = calcular_totales_checkout(item)
    except Exception as exc:
        totals_error = str(exc)
        totals = {
            'precio_unitario': item.precio or 'Por confirmar',
            'cantidad': 1,
            'subtotal': item.precio or 'Por confirmar',
            'marketplace_fee_percent': item.empresa.marketplace_fee_percent or 0,
            'marketplace_fee_amount': '-',
            'total': item.precio or 'Por confirmar',
            'valor_neto_empresa': '-',
        }

    checkout_token = uuid.uuid4().hex
    initial = {
        'checkout_token': checkout_token,
        'submitted_at': timezone.now().isoformat(),
        'website': '',
    }

    if request.method == 'GET':
        if request.user.is_authenticated:
            initial.update({
                'comprador_nombre': request.user.get_full_name() or request.user.username,
                'comprador_email': request.user.email,
                'nombre_contacto': request.user.get_full_name() or request.user.username,
            })
        _set_checkout_context(request, item, checkout_token)
        form = MarketplaceCheckoutForm(initial=initial)
    else:
        form = MarketplaceCheckoutForm(request.POST)
        session_ctx = _get_checkout_context(request).get(str(item.id), {})
        submitted_token = form.data.get('checkout_token', '')
        if not session_ctx or submitted_token != session_ctx.get('token'):
            form.add_error(None, 'Tu sesion de compra expiro. Vuelve a abrir el checkout.')

        if form.is_valid():
            if existe_pedido_pendiente_repetido(item, request.user):
                form.add_error(None, 'Ya tienes un pedido pendiente para esta publicacion.')
            else:
                buyer_key = f"mp:checkout:user:{request.user.id}:item:{item.id}"
                ip_key = f"mp:checkout:ip:{_client_ip(request)}:item:{item.id}"
                idempotency_key = hashlib.sha256(f"{request.user.id}:{item.id}:{submitted_token}".encode('utf-8')).hexdigest()

                if not cache.add(idempotency_key, True, timeout=CHECKOUT_IDEMPOTENCY_SECONDS):
                    form.add_error(None, 'Ya recibimos una solicitud reciente para este pedido. Espera un momento.')
                elif not cache.add(buyer_key, timezone.now().timestamp(), timeout=CHECKOUT_COOLDOWN_SECONDS):
                    form.add_error(None, 'Espera unos segundos antes de volver a enviar este pedido.')
                elif not cache.add(ip_key, timezone.now().timestamp(), timeout=CHECKOUT_COOLDOWN_SECONDS):
                    form.add_error(None, 'Detectamos demasiados intentos desde esta conexion. Intenta mas tarde.')
                else:
                    try:
                        pedido, _pago = crear_pedido_marketplace(
                            item=item,
                            form_data=form.cleaned_data,
                            comprador=request.user,
                        )
                    except Exception as exc:
                        form.add_error(None, str(exc))
                    else:
                        enviar_notificaciones_pedido_marketplace(pedido)
                        _clear_checkout_context(request, item.id)
                        messages.success(request, 'Tu pedido fue registrado correctamente.')
                        return redirect('marketplace:checkout_detail', numero_pedido=pedido.numero_pedido)

    context = {
        'item': item,
        'empresa': item.empresa,
        'form': form,
        'totals': totals,
        'checkout_token': checkout_token,
        'totals_error': totals_error,
    }
    context.update(_marketplace_user_context(request))
    return render(request, 'marketplace/checkout_form.html', context)


@login_required(login_url='/marketplace/login/')
@require_http_methods(["GET"])
def marketplace_checkout_detail_view(request, numero_pedido):
    pedido = get_object_or_404(
        MarketplacePedido.objects
        .select_related('empresa', 'direccion_entrega', 'pago', 'liquidacion_empresa', 'comprador')
        .prefetch_related('items__item'),
        numero_pedido=numero_pedido,
    )

    if not _user_can_view_pedido(request, pedido):
        messages.error(request, 'No tienes permisos para ver este pedido.')
        return redirect('marketplace:login')

    pedido_item = pedido.items.select_related('item', 'pedido').first()
    pedido_producto = pedido_item.item if pedido_item else None

    context = {
        'pedido': pedido,
        'empresa': pedido.empresa,
        'pago': getattr(pedido, 'pago', None),
        'liquidacion': getattr(pedido, 'liquidacion_empresa', None),
        'pedido_producto': pedido_producto,
        'es_admin_empresa': hasattr(request.user, 'perfil_marketing')
        and request.user.perfil_marketing.activo
        and request.user.perfil_marketing.empresa_id == pedido.empresa_id,
    }
    context.update(_marketplace_user_context(request))
    return render(request, 'marketplace/checkout_detail.html', context)


@login_required(login_url='/marketplace/login/')
@marketplace_buyer_required
@require_http_methods(["GET"])
def marketplace_order_list_view(request):
    pedidos = (
        MarketplacePedido.objects
        .filter(comprador=request.user)
        .select_related('empresa', 'pago')
        .prefetch_related('items')
        .order_by('-created_at')
    )
    context = {
        'pedidos': pedidos,
    }
    context.update(_marketplace_user_context(request))
    return render(request, 'marketplace/order_list.html', context)
