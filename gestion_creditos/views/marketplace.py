from .common import *


def _attach_marketplace_branding(empresa):
    if not empresa:
        return empresa
    empresa.logo_marketplace = MARKETPLACE_COMPANY_LOGOS.get((empresa.slug or '').lower(), '')
    empresa.logo_marketplace_url = ''
    empresa.logo_marketplace_static = ''

    logo_field = getattr(empresa, 'logo', None)
    if logo_field and getattr(logo_field, 'name', ''):
        try:
            empresa.logo_marketplace_url = logo_field.url
        except Exception:
            logger.warning("No fue posible resolver el logo de marketplace para %s", empresa.slug or empresa.nombre)

    if not empresa.logo_marketplace_url and empresa.logo_marketplace:
        empresa.logo_marketplace_static = empresa.logo_marketplace
    return empresa


def _marketplace_user_context(request):
    user = getattr(request, 'user', None)
    is_authenticated = bool(user and user.is_authenticated)
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


def marketplace_general_view(request):
    """
    Marketplace público general.
    Muestra publicaciones aprobadas de todas las empresas aliadas.
    """
    items = (
        MarketplaceItem.objects
        .filter(
            estado=MarketplaceItem.EstadoItem.APROBADO,
            empresa__tipo_empresa__in=[Empresa.TipoEmpresa.MARKETPLACE_EXTERNA, Empresa.TipoEmpresa.MIXTA],
        )
        .select_related('empresa')
        .order_by('-fecha_publicacion', '-fecha_creacion')
    )

    empresas_aliadas = (
        Empresa.objects
        .filter(
            tipo_empresa__in=[Empresa.TipoEmpresa.MARKETPLACE_EXTERNA, Empresa.TipoEmpresa.MIXTA],
            marketplace_items__estado=MarketplaceItem.EstadoItem.APROBADO,
        )
        .annotate(
            publicaciones_activas=Count(
                'marketplace_items',
                filter=Q(marketplace_items__estado=MarketplaceItem.EstadoItem.APROBADO),
                distinct=True
            )
        )
        .order_by('-publicaciones_activas', 'nombre')
        .distinct()
    )

    for item in items:
        _attach_marketplace_branding(item.empresa)

    for empresa in empresas_aliadas:
        _attach_marketplace_branding(empresa)

    context = {
        'items': items,
        'empresas_aliadas': empresas_aliadas,
        'total_items': items.count(),
        'total_empresas': empresas_aliadas.count(),
    }
    context.update(_marketplace_user_context(request))
    return render(request, 'marketplace/general.html', context)


def marketplace_empresa_view(request, empresa_slug):
    """
    Vitrina p?blica por empresa aliada.
    Solo muestra publicaciones aprobadas para una empresa específica.
    """
    empresa = get_object_or_404(
        Empresa,
        slug=empresa_slug,
        tipo_empresa__in=[Empresa.TipoEmpresa.MARKETPLACE_EXTERNA, Empresa.TipoEmpresa.MIXTA],
    )
    _attach_marketplace_branding(empresa)

    items = MarketplaceItem.objects.filter(
        empresa=empresa,
        estado=MarketplaceItem.EstadoItem.APROBADO
    ).order_by('-fecha_creacion')

    context = {
        'empresa': empresa,
        'items': items,
    }
    context.update(_marketplace_user_context(request))
    return render(request, 'marketplace/index.html', context)


@login_required(login_url='/marketplace/panel/login/')
@marketplace_admin_required
def marketplace_panel_view(request):
    items = (
        MarketplaceItem.objects
        .filter(empresa=request.empresa_marketing)
        .prefetch_related('historial_estados__usuario')
        .order_by('-fecha_creacion')
    )
    total_aprobados = items.filter(estado=MarketplaceItem.EstadoItem.APROBADO).count()
    total_pendientes = items.filter(estado=MarketplaceItem.EstadoItem.PENDIENTE).count()
    context = {
        'empresa': request.empresa_marketing,
        'items': items,
        'total_aprobados': total_aprobados,
        'total_pendientes': total_pendientes,
    }
    return render(request, 'marketplace/panel_list.html', context)


@login_required(login_url='/marketplace/panel/login/')
@marketplace_admin_required
def marketplace_item_create_view(request):
    if request.method == 'POST':
        form = MarketplaceItemForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save(commit=False)
            item.empresa = request.empresa_marketing
            # Al crear, siempre queda pendiente de aprobacion.
            item.estado = MarketplaceItem.EstadoItem.PENDIENTE
            item.save()
            registrar_historial_publicacion(
                item=item,
                estado_anterior='',
                estado_nuevo=item.estado,
                usuario=request.user,
                origen=MarketplaceItemHistorialEstado.OrigenCambio.EMPRESA,
                comentario='Publicacion creada desde el panel de empresa.'
            )
            messages.success(request, 'Publicacion enviada a aprobacion.')
            return redirect('marketplace:panel')
    else:
        form = MarketplaceItemForm()

    return render(request, 'marketplace/panel_form.html', {
        'form': form,
        'empresa': request.empresa_marketing,
        'is_edit': False,
    })


@login_required(login_url='/marketplace/panel/login/')
@marketplace_admin_required
def marketplace_item_edit_view(request, item_id):
    item = get_object_or_404(MarketplaceItem, id=item_id, empresa=request.empresa_marketing)

    if request.method == 'POST':
        form = MarketplaceItemForm(request.POST, request.FILES, instance=item)
        if form.is_valid():
            estado_anterior = item.estado
            updated_item = form.save(commit=False)
            updated_item.save()
            if estado_anterior != MarketplaceItem.EstadoItem.PENDIENTE:
                try:
                    # Cualquier edicion de un item publicado/rechazado/inactivo vuelve a revision.
                    cambiar_estado_publicacion(
                        item=updated_item,
                        estado_nuevo=MarketplaceItem.EstadoItem.PENDIENTE,
                        usuario=request.user,
                        origen=MarketplaceItemHistorialEstado.OrigenCambio.EMPRESA,
                        comentario='Publicacion editada por la empresa y enviada a revision.'
                    )
                except ValidationError as exc:
                    messages.error(request, str(exc))
                    return redirect('marketplace:panel')
            messages.success(request, 'Publicacion actualizada y enviada a revision.')
            return redirect('marketplace:panel')
    else:
        form = MarketplaceItemForm(instance=item)

    return render(request, 'marketplace/panel_form.html', {
        'form': form,
        'empresa': request.empresa_marketing,
        'item': item,
        'is_edit': True,
    })


@login_required(login_url='/marketplace/panel/login/')
@require_POST
@marketplace_admin_required
def marketplace_item_deactivate_view(request, item_id):
    item = get_object_or_404(MarketplaceItem, id=item_id, empresa=request.empresa_marketing)
    try:
        cambiar_estado_publicacion(
            item=item,
            estado_nuevo=MarketplaceItem.EstadoItem.INACTIVO,
            usuario=request.user,
            origen=MarketplaceItemHistorialEstado.OrigenCambio.EMPRESA,
            comentario='Publicacion desactivada desde el panel de empresa.'
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect('marketplace:panel')
    messages.success(request, 'Publicacion desactivada.')
    return redirect('marketplace:panel')
