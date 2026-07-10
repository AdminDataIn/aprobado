from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMultiAlternatives
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib import messages
from django.contrib.auth import logout, login as auth_login
from django.views.generic import TemplateView
from django.urls import reverse
from django.utils.http import urlencode as django_urlencode, url_has_allowed_host_and_scheme
from decimal import Decimal
from django.utils import timezone
from django.db import transaction

from gestion_creditos.models import AsesorComercial, Credito, Empresa
from gestion_creditos.services.libranza_rules import LIBRANZA_MONTO_MAXIMO, LIBRANZA_MONTO_MINIMO_PUBLICO
from gestion_creditos.services.tasa_service import obtener_tasa_credito
from .forms import (
    EmailAuthenticationForm,
    ExecutiveAuthenticationForm,
    InvestorAuthenticationForm,
    InvestorActivationForm,
    MarketplaceBuyerRegistrationForm,
    PagadorAuthenticationForm,
    PagadorActivationForm,
    PagadorPasswordResetRequestForm,
    ProductUserRegistrationForm,
)
from .executive_activation_service import (
    buscar_token_ejecutivo,
    enviar_invitacion_activacion_ejecutivo,
    marcar_token_ejecutivo_como_usado,
)
from .models import InvestorAccessToken, PagadorAccessToken, PerfilEmpresaMarketing, ProductAccessProfile
from .product_flow import (
    ProductFlowConflict,
    assign_user_flow,
    get_flow_home_path,
    get_flow_label,
    get_user_flow,
)
from .pagador_activation_service import (
    buscar_token_vigente,
    enviar_invitacion_activacion_pagador,
    marcar_token_como_usado,
    obtener_perfil_pagador_por_identificador,
    enviar_reset_password_pagador,
)
from .investor_activation_service import (
    buscar_token_inversionista,
    enviar_invitacion_inversionista,
    marcar_token_inversionista_como_usado,
)


def _send_marketplace_welcome_email(user):
    if not user.email:
        return

    context = {
        'user': user,
        'display_name': user.first_name or user.get_full_name() or user.email,
    }
    body = render_to_string('emails/marketplace/marketplace_welcome.txt', context)
    html_body = render_to_string('emails/marketplace/marketplace_welcome.html', context)
    email = EmailMultiAlternatives(
        subject='Bienvenido al marketplace de Aprobado',
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    email.attach_alternative(html_body, 'text/html')
    email.send(fail_silently=True)


def _get_safe_next_url(request, default_url):
    next_url = request.POST.get('next') or request.GET.get('next') or ''
    if next_url and next_url.startswith('/') and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return default_url


def _with_next(base_url, next_url):
    if not next_url:
        return base_url
    parsed = urlsplit(base_url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_params['next'] = next_url
    return urlunsplit(parsed._replace(query=urlencode(query_params)))


def _build_google_login_url(next_url):
    query = urlencode({
        'process': 'login',
        'next': next_url,
    })
    return f"/accounts/google/login/?{query}"


def _get_token_invalid_reason(access_token):
    if not access_token:
        return 'not_found'
    if access_token.used_at:
        return 'used'
    if access_token.invalidated_at:
        return 'replaced'
    if access_token.expires_at <= timezone.now():
        return 'expired'
    return 'unknown'


# Create your views here.
def index(request):
    return render(request, 'index.html')


def login_dispatch_view(request):
    next_url = _get_safe_next_url(request, '/')
    normalized_next = next_url.lower()
    if normalized_next.startswith('/libranza/'):
        destination = '/libranza/login/'
    elif normalized_next.startswith('/emprendimiento/'):
        destination = '/emprendimiento/login/'
    elif normalized_next.startswith('/pagador/'):
        destination = '/pagador/login/'
    elif normalized_next.startswith('/marketplace/panel/'):
        destination = '/marketplace/panel/login/'
    elif normalized_next.startswith('/marketplace/empresa/'):
        destination = '/marketplace/panel/login/'
    elif normalized_next.startswith('/marketplace/'):
        destination = '/marketplace/login/'
    elif normalized_next.startswith('/inversionista/'):
        destination = '/inversionista/login/'
    else:
        destination = '/accounts/login/'
    return redirect(_with_next(destination, next_url))

#def aplicar_formulario(request):
#    return render(request, 'emprendimiento/aplicando.html')

@login_required(login_url='/emprendimiento/login/')
def aplicar_formulario(request):
    # if not SocialAccount.objects.filter(user=request.user, provider='google').exists():
        # return redirect('/accounts/google/login/next=/emprendimiento/solicitar/')
    return render(request, 'emprendimiento/aplicando.html')


# Simulador de EMPRENDIMIENTO
def simulador(request):
    """
    Vista del simulador de crédito de EMPRENDIMIENTO.
    Siempre muestra el simulador de emprendimiento, independiente del grupo del usuario.
    """
    context = {
        'es_empleado': False  # SIEMPRE False porque este es el simulador de EMPRENDIMIENTO
    }
    return render(request, 'emprendimiento/simulacion.html', context)


# def simulador(request):
#     #* Por defecto el usuario no es una empresa
#     es_empleado = False

#     #* Verificamos si el usuario está autenticado y pertenece al grupo "Empresas"
#     if request.user.is_authenticated and request.user.groups.filter(name='Empresas').exists():
#         es_empleado = True

#     #* Pasamos la variable 'es_empleadoado' al contexto del template
#     context = {
#         'es_empleado': es_empleado
#     }
#     return render(request, 'emprendimiento/simulacion.html', context)


class EmpresaLoginView(LoginView):
    template_name = 'account/pagador/login.html'
    form_class = PagadorAuthenticationForm
    redirect_authenticated_user = False  # ⭐ Cambiado a False para permitir acceso a usuarios autenticados

    def get(self, request, *args, **kwargs):
        # Si el usuario ya está autenticado, verificamos si tiene perfil de pagador
        if request.user.is_authenticated:
            if hasattr(request.user, 'perfil_pagador'):
                # Si ya tiene perfil de pagador, lo redirigimos al dashboard
                return redirect(reverse('pagador:dashboard'))
            else:
                # Si está autenticado pero NO es pagador, mostramos mensaje y redirigimos
                messages.warning(request, 'Su cuenta actual no tiene permisos de pagador. Si necesita acceso como pagador, contacte al administrador.')
                return redirect(reverse('libranza:landing'))

        # Marcamos la sesión para identificar que el flujo de login empezó aquí
        request.session['login_flow'] = 'empresa'
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        # Verificamos si el usuario tiene un perfil de pagador asociado
        if hasattr(user, 'perfil_pagador'):
            return super().form_valid(form)
        else:
            # Si no tiene perfil de pagador, rechazamos el login
            logout(self.request)
            messages.error(self.request, 'Este usuario no tiene permisos para acceder como pagador.')
            return self.form_invalid(form)

    def get_success_url(self):
        return self.get_redirect_url() or reverse('pagador:dashboard')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = _get_safe_next_url(self.request, reverse('pagador:dashboard'))
        context['forgot_password_url'] = reverse('pagador:password_reset_request')
        context['back_url'] = reverse('home')
        return context


class AsesorLoginView(LoginView):
    template_name = 'account/asesor/login.html'
    form_class = ExecutiveAuthenticationForm
    redirect_authenticated_user = False

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            asesor = getattr(request.user, 'asesor_comercial', None)
            if asesor and asesor.activo:
                return redirect(reverse('ejecutivos:dashboard'))
            messages.warning(request, 'Su cuenta actual no tiene permisos de ejecutivo.')
            return redirect(reverse('libranza:landing'))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        asesor = getattr(user, 'asesor_comercial', None)
        if asesor and asesor.activo:
            return super().form_valid(form)
        logout(self.request)
        messages.error(self.request, 'Este usuario no tiene permisos para acceder como ejecutivo.')
        return self.form_invalid(form)

    def get_success_url(self):
        return self.get_redirect_url() or reverse('ejecutivos:dashboard')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = _get_safe_next_url(self.request, reverse('ejecutivos:dashboard'))
        context['back_url'] = reverse('libranza:landing')
        return context


def executive_activate_account_view(request, token):
    access_token = buscar_token_ejecutivo(token)
    if not access_token:
        return render(request, 'account/asesor/activate_account.html', {
            'token_valido': False,
            'expirado': True,
            'form': None,
            'invalid_reason': 'not_found',
            'permite_reenvio': False,
        })

    expirado = access_token.expires_at <= timezone.now() or access_token.used_at or access_token.invalidated_at
    if expirado:
        if request.method == 'POST' and request.POST.get('action') == 'resend_activation':
            try:
                enviar_invitacion_activacion_ejecutivo(access_token.asesor, force_new=False)
                messages.success(
                    request,
                    f'Enviamos un nuevo enlace de activación a {access_token.email_destino}. Usa solo el correo más reciente.'
                )
                return redirect('ejecutivos:login')
            except Exception as exc:
                messages.error(request, f'No se pudo reenviar el enlace de activación: {exc}')
        return render(request, 'account/asesor/activate_account.html', {
            'token_valido': False,
            'expirado': True,
            'form': None,
            'usuario_email': access_token.email_destino,
            'invalid_reason': _get_token_invalid_reason(access_token),
            'permite_reenvio': True,
            'asesor': access_token.asesor,
        })

    user = access_token.usuario
    if request.method == 'POST':
        form = PagadorActivationForm(user, request.POST)
        if form.is_valid():
            form.save()
            user.is_active = True
            user.save(update_fields=['is_active', 'password'])
            marcar_token_ejecutivo_como_usado(access_token)
            messages.success(request, 'Tu acceso como ejecutivo fue activado correctamente. Ya puedes iniciar sesión.')
            return redirect('ejecutivos:login')
    else:
        form = PagadorActivationForm(user)

    return render(request, 'account/asesor/activate_account.html', {
        'token_valido': True,
        'expirado': False,
        'form': form,
        'usuario_email': access_token.email_destino,
        'asesor': access_token.asesor,
        'permite_reenvio': False,
    })


def pagador_activate_account_view(request, token):
    """
    Permite que el pagador defina su contrasena inicial mediante un enlace
    temporal. El login actual no cambia para usuarios ya activos.
    """
    access_token = buscar_token_vigente(token, tipo=PagadorAccessToken.TipoToken.ACTIVACION)
    if not access_token:
        return render(request, 'account/pagador/activate_account.html', {
            'token_valido': False,
            'expirado': True,
            'form': None,
            'invalid_reason': 'not_found',
            'permite_reenvio': False,
        })

    expirado = access_token.expires_at <= timezone.now() or access_token.used_at or access_token.invalidated_at
    if expirado:
        if request.method == 'POST' and request.POST.get('action') == 'resend_activation':
            try:
                enviar_invitacion_activacion_pagador(access_token.perfil_pagador, force_new=False)
                messages.success(
                    request,
                    f'Enviamos un nuevo enlace de activacion a {access_token.email_destino}. Usa solo el correo mas reciente.'
                )
                return redirect('pagador:login')
            except Exception as exc:
                messages.error(request, f'No se pudo reenviar el enlace de activacion: {exc}')
        return render(request, 'account/pagador/activate_account.html', {
            'token_valido': False,
            'expirado': True,
            'form': None,
            'usuario_email': access_token.email_destino,
            'invalid_reason': _get_token_invalid_reason(access_token),
            'permite_reenvio': True,
            'modo': 'activacion',
        })

    user = access_token.usuario
    if request.method == 'POST':
        form = PagadorActivationForm(user, request.POST)
        if form.is_valid():
            form.save()
            user.is_active = True
            user.save(update_fields=['is_active', 'password'])
            marcar_token_como_usado(access_token)
            messages.success(request, 'Tu acceso como pagador fue activado correctamente. Ya puedes iniciar sesion.')
            return redirect('pagador:login')
    else:
        form = PagadorActivationForm(user)

    return render(request, 'account/pagador/activate_account.html', {
        'token_valido': True,
        'expirado': False,
        'form': form,
        'usuario_email': access_token.email_destino,
        'empresa': access_token.perfil_pagador.empresa,
        'modo': 'activacion',
        'permite_reenvio': False,
    })


def pagador_password_reset_request_view(request):
    """
    Solicita un enlace de restablecimiento sin exponer si la cuenta existe.
    """
    if request.method == 'POST':
        form = PagadorPasswordResetRequestForm(request.POST)
        if form.is_valid():
            perfil_pagador = obtener_perfil_pagador_por_identificador(form.cleaned_data['email'])
            if perfil_pagador:
                try:
                    enviar_reset_password_pagador(perfil_pagador)
                except Exception:
                    pass
            messages.success(
                request,
                'Si encontramos una cuenta de pagador asociada, enviamos un enlace de restablecimiento al correo registrado.'
            )
            return redirect('pagador:password_reset_request')
    else:
        form = PagadorPasswordResetRequestForm()

    return render(request, 'account/pagador/password_reset_request.html', {'form': form})


def pagador_password_reset_confirm_view(request, token):
    """
    Permite redefinir la contrasena usando un token temporal de reset.
    """
    access_token = buscar_token_vigente(token, tipo=PagadorAccessToken.TipoToken.RESET_PASSWORD)
    if not access_token:
        return render(request, 'account/pagador/activate_account.html', {
            'token_valido': False,
            'expirado': True,
            'form': None,
            'modo': 'reset',
            'invalid_reason': 'not_found',
            'permite_reenvio': False,
        })

    expirado = access_token.expires_at <= timezone.now() or access_token.used_at or access_token.invalidated_at
    if expirado:
        return render(request, 'account/pagador/activate_account.html', {
            'token_valido': False,
            'expirado': True,
            'form': None,
            'usuario_email': access_token.email_destino,
            'modo': 'reset',
            'invalid_reason': _get_token_invalid_reason(access_token),
            'permite_reenvio': False,
        })

    user = access_token.usuario
    if request.method == 'POST':
        form = PagadorActivationForm(user, request.POST)
        if form.is_valid():
            form.save()
            if not user.is_active:
                user.is_active = True
                user.save(update_fields=['is_active', 'password'])
            marcar_token_como_usado(access_token)
            messages.success(request, 'Tu contrasena fue actualizada correctamente. Ya puedes iniciar sesion.')
            return redirect('pagador:login')
    else:
        form = PagadorActivationForm(user)

    return render(request, 'account/pagador/activate_account.html', {
        'token_valido': True,
        'expirado': False,
        'form': form,
        'usuario_email': access_token.email_destino,
        'empresa': access_token.perfil_pagador.empresa,
        'modo': 'reset',
        'permite_reenvio': False,
    })


class MarketingLoginView(LoginView):
    template_name = 'account/marketplace_admin/login_marketing.html'
    redirect_authenticated_user = False

    def get(self, request, *args, **kwargs):
        # Si ya está autenticado y tiene perfil marketing activo, va directo al panel.
        if request.user.is_authenticated:
            if hasattr(request.user, 'perfil_marketing') and request.user.perfil_marketing.activo:
                return redirect(reverse('marketplace:panel'))
            messages.warning(request, 'Su cuenta actual no tiene acceso activo al panel marketplace.')
            return redirect(reverse('home'))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        if hasattr(user, 'perfil_marketing') and user.perfil_marketing.activo:
            return super().form_valid(form)
        logout(self.request)
        messages.error(self.request, 'Este usuario no tiene permisos para ingresar al panel marketplace.')
        return self.form_invalid(form)

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        if next_url:
            return next_url
        return reverse('marketplace:panel')


class MarketplaceAdminLoginView(MarketingLoginView):
    template_name = 'account/marketplace_admin/login.html'

    def _empresa_slug_requerida(self):
        return self.kwargs.get('empresa_slug')

    def _usuario_tiene_empresa(self, user):
        if not hasattr(user, 'perfil_marketing') or not user.perfil_marketing.activo:
            return False
        empresa_slug = self._empresa_slug_requerida()
        if not empresa_slug:
            return True
        return user.perfil_marketing.empresa.slug == empresa_slug

    def get(self, request, *args, **kwargs):
        empresa_slug = self._empresa_slug_requerida()
        if request.user.is_authenticated:
            if self._usuario_tiene_empresa(request.user):
                return redirect(reverse('marketplace:panel'))
            messages.warning(request, 'Tu cuenta no tiene acceso al panel de la empresa solicitada.')
            return redirect(reverse('marketplace:home'))

        if empresa_slug and not PerfilEmpresaMarketing.objects.filter(empresa__slug=empresa_slug, activo=True).exists():
            messages.error(request, 'La empresa solicitada no tiene panel marketplace habilitado.')
            return redirect(reverse('marketplace:home'))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        if not self._usuario_tiene_empresa(user):
            messages.error(self.request, 'Este usuario no tiene permisos para ingresar al panel de esta empresa.')
            return self.form_invalid(form)
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['empresa_panel'] = self.kwargs.get('empresa_slug', '')
        context['forgot_password_url'] = reverse('marketplace:admin_password_reset')
        context['back_url'] = reverse('marketplace:home')
        return context


# Vista para la Landing Page de Crédito de Libranza
_CENTROIDES_COLOMBIA = {
    ('bogota dc', 'bogota'): (4.7110, -74.0721),
    ('cundinamarca', 'bogota'): (4.7110, -74.0721),
    ('meta', 'villavicencio'): (4.1420, -73.6266),
    ('antioquia', 'medellin'): (6.2442, -75.5812),
    ('casanare', 'yopal'): (5.3378, -72.3959),
    ('valle del cauca', 'cali'): (3.4516, -76.5320),
    ('atlantico', 'barranquilla'): (10.9685, -74.7813),
    ('santander', 'bucaramanga'): (7.1193, -73.1227),
    ('bolivar', 'cartagena'): (10.3910, -75.4794),
    ('caldas', 'manizales'): (5.0703, -75.5138),
    ('quindio', 'armenia'): (4.5339, -75.6811),
}


def _normalizar_texto_ubicacion(valor):
    import unicodedata

    texto = str(valor or '').strip().lower()
    texto = ''.join(
        caracter
        for caracter in unicodedata.normalize('NFKD', texto)
        if not unicodedata.combining(caracter)
    )
    return ' '.join(texto.replace('.', ' ').replace(',', ' ').split())


def _iniciales_empresa(nombre):
    partes = [parte for parte in str(nombre or '').split() if parte]
    if not partes:
        return 'AP'
    return ''.join(parte[0].upper() for parte in partes[:2])


def _extraer_ubicacion_empresa(empresa):
    ciudad = (
        getattr(empresa, 'ciudad', '')
        or getattr(empresa, 'ciudad_empresa', '')
        or getattr(empresa, 'municipio', '')
    )
    departamento = (
        getattr(empresa, 'departamento', '')
        or getattr(empresa, 'departamento_empresa', '')
    )
    latitud = getattr(empresa, 'latitud', None)
    longitud = getattr(empresa, 'longitud', None)

    if latitud is None or longitud is None:
        llave = (_normalizar_texto_ubicacion(departamento), _normalizar_texto_ubicacion(ciudad))
        coordenadas = _CENTROIDES_COLOMBIA.get(llave)
        if coordenadas:
            latitud, longitud = coordenadas

    if not ciudad and not departamento:
        return None

    return {
        'ciudad': ciudad or departamento,
        'municipio': ciudad or departamento,
        'departamento': departamento or 'Colombia',
        'latitud': float(latitud) if latitud is not None else None,
        'longitud': float(longitud) if longitud is not None else None,
    }


def _build_landing_trusted_companies():
    companies = (
        Empresa.objects
        .filter(convenio_activo=True)
        .order_by('nombre')
    )
    trusted_companies = []
    for company in companies:
        logo_url = None
        try:
            logo_url = company.logo.url
        except Exception:
            logo_url = None
        ubicacion = _extraer_ubicacion_empresa(company)
        trusted_companies.append({
            'nombre': company.nombre,
            'logo_url': logo_url,
            'iniciales': _iniciales_empresa(company.nombre),
            'ubicacion': ubicacion,
            'slug': company.slug,
        })
    return trusted_companies


def _build_landing_presencia(companies):
    ubicaciones = {}
    sin_ubicacion = 0

    for company in companies:
        ubicacion = company.get('ubicacion') or {}
        ciudad = ubicacion.get('ciudad') or ubicacion.get('municipio')
        departamento = ubicacion.get('departamento')
        latitud = ubicacion.get('latitud')
        longitud = ubicacion.get('longitud')
        if not ciudad or not departamento or latitud is None or longitud is None:
            sin_ubicacion += 1
            continue

        llave = (
            _normalizar_texto_ubicacion(departamento),
            _normalizar_texto_ubicacion(ciudad),
        )
        if llave not in ubicaciones:
            ubicaciones[llave] = {
                'ciudad': ciudad,
                'municipio': ciudad,
                'departamento': departamento,
                'latitud': latitud,
                'longitud': longitud,
                'empresas': 0,
            }
        ubicaciones[llave]['empresas'] += 1

    mapa_ubicaciones = sorted(
        ubicaciones.values(),
        key=lambda item: (-item['empresas'], item['departamento'], item['ciudad']),
    )
    departamentos = sorted({item['departamento'] for item in mapa_ubicaciones})
    ciudades = sorted({item['ciudad'] for item in mapa_ubicaciones})
    return {
        'con_ubicacion_registrada': sum(item['empresas'] for item in mapa_ubicaciones),
        'sin_ubicacion_registrada': sin_ubicacion,
        'departamentos_con_presencia': departamentos,
        'ciudades_con_presencia': ciudades,
        'top_zonas': mapa_ubicaciones[:8],
        'mapa_ubicaciones': mapa_ubicaciones,
    }


def _build_landing_backers():
    respaldos = [
        {
            'nombre': 'DataCrédito Experian',
            'logo_path': 'images/respaldos/datacredito-experian.png',
            'descripcion': 'Información para fortalecer validaciones de riesgo cuando aplique.',
        },
        {
            'nombre': 'FiGarantías',
            'logo_path': 'images/respaldos/figarantias.svg',
            'descripcion': 'Garantías que respaldan procesos de crédito de forma institucional.',
        },
        {
            'nombre': 'Orinoco TIC',
            'logo_path': 'images/respaldos/orinoco-tic.png',
            'descripcion': 'Tecnología para soportar operación digital y trazabilidad.',
        },
        {
            'nombre': 'Seguros SURA',
            'logo_path': 'images/respaldos/seguros-sura.svg',
            'descripcion': 'Seguro de vida deudores para acompañar el respaldo del crédito.',
        },
    ]
    return [respaldo for respaldo in respaldos if finders.find(respaldo['logo_path'])]


def libranza_landing(request):
    """
    Vista para mostrar la landing page del producto de Crédito de Libranza.

    Esta página es pública y no requiere autenticación.
    Muestra información completa sobre el producto incluyendo:
    - Características y beneficios
    - Simulador (enlace al simulador completo)
    - Proceso paso a paso
    - Requisitos y documentación
    - Preguntas frecuentes
    - Llamados a la acción para solicitar el crédito

    Returns:
        Renderiza 'libranza/libranza_landing.html'
    """
    tasa_libranza = obtener_tasa_credito(Credito.LineaCredito.LIBRANZA)
    tasa_libranza_decimal = tasa_libranza / Decimal('100')
    landing_trusted_companies = _build_landing_trusted_companies()
    context = {
        'libranza_tasa_mensual': tasa_libranza,
        'libranza_tasa_decimal': tasa_libranza_decimal,
        'libranza_tasa_decimal_js': format(tasa_libranza_decimal, 'f'),
        'libranza_monto_minimo_publico': LIBRANZA_MONTO_MINIMO_PUBLICO,
        'libranza_monto_maximo': LIBRANZA_MONTO_MAXIMO,
        'adelanto_tasa_mensual': obtener_tasa_credito(Credito.LineaCredito.ADELANTO_NOMINA),
        'adelanto_comision_percent': Decimal(str(getattr(settings, 'ADELANTO_NOMINA_COMISION_PERCENT', '10'))),
        'landing_social_links': [
            {
                'label': 'Instagram',
                'icon': 'bi-instagram',
                'url': 'https://www.instagram.com/_aprobado.co?igsh=emY4NWJ1Z2JzZGM5',
            },
            {
                'label': 'Facebook',
                'icon': 'bi-facebook',
                'url': 'https://www.facebook.com/share/1AdTG9Qbim/?mibextid=wwXIfr',
            },
        ],
        'landing_trusted_companies': landing_trusted_companies,
        'landing_presencia': _build_landing_presencia(landing_trusted_companies),
        'landing_backers': _build_landing_backers(),
        'landing_testimonials_enabled': False,
    }
    return render(request, 'libranza/libranza_landing.html', context)


# Vista para el Simulador de Crédito de Libranza
def simulador_libranza(request):
    """
    Vista para mostrar el simulador exclusivo de Crédito de Libranza.

    Esta página es pública y no requiere autenticación.
    Permite calcular:
    - Monto solicitado: $500.000 - $3.000.000
    - Plazo: 1 - 6 meses
    - Comisión: 10% + IVA (19%)
    - Afianzadora: 4% + IVA (próximamente)
    - Cuota mensual
    - Total a pagar

    Returns:
        Renderiza 'libranza/simulacion_libranza.html'
    """
    tasa_libranza = obtener_tasa_credito(Credito.LineaCredito.LIBRANZA)
    tasa_libranza_decimal = tasa_libranza / Decimal('100')
    context = {
        'libranza_tasa_mensual': tasa_libranza,
        'libranza_tasa_decimal': tasa_libranza_decimal,
        'libranza_tasa_decimal_js': format(tasa_libranza_decimal, 'f'),
        'libranza_monto_minimo_publico': LIBRANZA_MONTO_MINIMO_PUBLICO,
        'libranza_monto_maximo': LIBRANZA_MONTO_MAXIMO,
    }
    return render(request, 'libranza/simulacion_libranza.html', context)


# Vista para el login de Libranza
class ProductLoginView(LoginView):
    form_class = EmailAuthenticationForm
    redirect_authenticated_user = True
    next_default_url = '/'
    target_flow = None

    def get_success_url(self):
        return self.get_redirect_url() or self.next_default_url

    def form_valid(self, form):
        user = form.get_user()
        if self.target_flow:
            try:
                assign_user_flow(user, self.target_flow)
            except ProductFlowConflict as exc:
                current_flow = exc.args[0] if exc.args else None
                form.add_error(
                    None,
                    f'Tu cuenta ya pertenece al flujo de {get_flow_label(current_flow)} y no puede usarse aqui.'
                )
                return self.form_invalid(form)
            self.request.session['authenticated_product_flow'] = self.target_flow
            self.request.session['producto_actual'] = self.target_flow
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        next_url = _get_safe_next_url(self.request, self.next_default_url)
        context['google_login_url'] = _build_google_login_url(next_url)
        context['next_url'] = next_url
        if getattr(self, 'registration_url_name', None):
            context['registration_url'] = _with_next(
                reverse(self.registration_url_name),
                next_url,
            )
        return context


class LoginLibranzaView(ProductLoginView):
    template_name = 'account/libranza/login.html'
    next_default_url = '/libranza/mi-credito/'
    target_flow = 'LIBRANZA'
    registration_url_name = 'libranza:register'


class LoginEmprendimientoView(ProductLoginView):
    template_name = 'account/emprendimiento/login.html'
    next_default_url = '/emprendimiento/mi-credito/'
    target_flow = 'EMPRENDIMIENTO'
    registration_url_name = 'emprendimiento:register'


class LoginInversionistaView(ProductLoginView):
    template_name = 'account/inversionista/login.html'
    form_class = InvestorAuthenticationForm
    next_default_url = '/inversionista/'
    target_flow = ProductAccessProfile.ProductFlow.INVERSIONISTA

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.pop('google_login_url', None)
        context['forgot_password_url'] = reverse('inversionista:password_reset')
        context['back_url'] = reverse('home')
        return context


class MarketplaceBuyerLoginView(ProductLoginView):
    template_name = 'account/marketplace_buyer/login.html'
    next_default_url = '/marketplace/'
    target_flow = ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER
    registration_url_name = 'marketplace:register'

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated and hasattr(request.user, 'perfil_marketing') and request.user.perfil_marketing.activo:
            messages.error(request, 'Tu cuenta pertenece al panel de empresa y no puede entrar como comprador.')
            return redirect(reverse('marketplace:admin_login_without_company'))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        if hasattr(user, 'perfil_marketing') and user.perfil_marketing.activo:
            messages.error(self.request, 'Tu cuenta pertenece al panel de empresa y no puede entrar como comprador.')
            return self.form_invalid(form)
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['registration_url'] = _with_next(
            reverse('marketplace:register'),
            context.get('next_url') or self.next_default_url,
        )
        return context


class MarketplaceBuyerRegisterView(TemplateView):
    template_name = 'account/marketplace_buyer/register.html'

    def get(self, request, *args, **kwargs):
        next_url = _get_safe_next_url(request, reverse('marketplace:home'))
        if request.user.is_authenticated:
            if hasattr(request.user, 'perfil_marketing') and request.user.perfil_marketing.activo:
                messages.error(request, 'Tu cuenta pertenece al panel de empresa y no puede registrarse como comprador.')
                return redirect(reverse('marketplace:admin_login_without_company'))
            current_flow = get_user_flow(request.user)
            if current_flow and current_flow != ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER:
                messages.error(
                    request,
                    f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede registrarse como comprador marketplace.'
                )
                return redirect(get_flow_home_path(current_flow))
            return redirect(next_url)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        next_url = _get_safe_next_url(request, reverse('marketplace:home'))
        form = MarketplaceBuyerRegistrationForm(
            request.POST,
            target_flow=ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER,
        )
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                assign_user_flow(user, ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER)
                request.session['authenticated_product_flow'] = ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER
                request.session['producto_actual'] = ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER
            auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            _send_marketplace_welcome_email(user)
            messages.success(request, 'Tu cuenta marketplace fue creada correctamente.')
            return redirect(next_url)
        return render(
            request,
            self.template_name,
            {
                'form': form,
                'google_login_url': _build_google_login_url(next_url),
                'next_url': next_url,
                'login_url': _with_next(reverse('marketplace:login'), next_url),
            },
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        next_url = _get_safe_next_url(self.request, reverse('marketplace:home'))
        context.setdefault(
            'form',
            MarketplaceBuyerRegistrationForm(
                target_flow=ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER
            ),
        )
        context['google_login_url'] = _build_google_login_url(next_url)
        context['next_url'] = next_url
        context['login_url'] = _with_next(reverse('marketplace:login'), next_url)
        return context


class ProductRegisterView(TemplateView):
    template_name = ''
    next_default_url = '/'
    target_flow = None
    login_url_name = ''
    landing_url_name = ''

    def get(self, request, *args, **kwargs):
        next_url = _get_safe_next_url(request, self.next_default_url)
        if request.user.is_authenticated:
            current_flow = get_user_flow(request.user)
            if current_flow and current_flow != self.target_flow:
                messages.error(
                    request,
                    f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede registrarse aqui.'
                )
                return redirect(get_flow_home_path(current_flow))
            return redirect(next_url)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        next_url = _get_safe_next_url(request, self.next_default_url)
        form = ProductUserRegistrationForm(request.POST, target_flow=self.target_flow)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                assign_user_flow(user, self.target_flow)
            auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            request.session['authenticated_product_flow'] = self.target_flow
            request.session['producto_actual'] = self.target_flow
            messages.success(request, 'Tu cuenta fue creada correctamente.')
            return redirect(next_url)
        return render(request, self.template_name, self._build_context(form))

    def _build_context(self, form):
        next_url = _get_safe_next_url(self.request, self.next_default_url)
        return {
            'form': form,
            'login_url': _with_next(reverse(self.login_url_name), next_url),
            'back_url': reverse(self.landing_url_name),
            'google_login_url': _build_google_login_url(next_url),
            'next_url': next_url,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            self._build_context(
                ProductUserRegistrationForm(target_flow=self.target_flow)
            )
        )
        return context


class LibranzaRegisterView(ProductRegisterView):
    template_name = 'account/libranza/register.html'
    next_default_url = '/libranza/mi-credito/'
    target_flow = ProductAccessProfile.ProductFlow.LIBRANZA
    login_url_name = 'libranza:login'
    landing_url_name = 'libranza:landing'


class EmprendimientoRegisterView(ProductRegisterView):
    template_name = 'account/emprendimiento/register.html'
    next_default_url = '/emprendimiento/mi-credito/'
    target_flow = ProductAccessProfile.ProductFlow.EMPRENDIMIENTO
    login_url_name = 'emprendimiento:login'
    landing_url_name = 'emprendimiento:landing'


def investor_activate_account_view(request, token):
    access_token = buscar_token_inversionista(token, tipo=InvestorAccessToken.TipoToken.ACTIVACION)
    if not access_token:
        return render(request, 'account/inversionista/activate_account.html', {
            'token_valido': False,
            'expirado': True,
            'form': None,
            'invalid_reason': 'not_found',
            'permite_reenvio': False,
        })

    expirado = access_token.expires_at <= timezone.now() or access_token.used_at or access_token.invalidated_at
    if expirado:
        if request.method == 'POST' and request.POST.get('action') == 'resend_activation':
            try:
                enviar_invitacion_inversionista(access_token.usuario, force_new=False)
                messages.success(
                    request,
                    f'Enviamos un nuevo enlace de activacion a {access_token.email_destino}. Usa solo el correo mas reciente.'
                )
                return redirect('inversionista:login')
            except Exception as exc:
                messages.error(request, f'No se pudo reenviar el enlace de activacion: {exc}')
        return render(request, 'account/inversionista/activate_account.html', {
            'token_valido': False,
            'expirado': True,
            'form': None,
            'usuario_email': access_token.email_destino,
            'invalid_reason': _get_token_invalid_reason(access_token),
            'permite_reenvio': True,
        })

    user = access_token.usuario
    if request.method == 'POST':
        form = InvestorActivationForm(user, request.POST)
        if form.is_valid():
            form.save()
            if not user.is_active:
                user.is_active = True
                user.save(update_fields=['is_active', 'password'])
            marcar_token_inversionista_como_usado(access_token)
            messages.success(request, 'Tu acceso como inversionista fue activado correctamente. Ya puedes iniciar sesion.')
            return redirect('inversionista:login')
    else:
        form = InvestorActivationForm(user)

    return render(request, 'account/inversionista/activate_account.html', {
        'token_valido': True,
        'expirado': False,
        'form': form,
        'usuario_email': access_token.email_destino,
        'permite_reenvio': False,
    })


class CustomLogoutView(LogoutView):
    http_method_names = ['post', 'options']

    def post(self, request, *args, **kwargs):
        producto_actual = request.session.get('producto_actual')
        current_flow = get_user_flow(request.user)

        if hasattr(request.user, 'perfil_marketing') and getattr(request.user.perfil_marketing, 'activo', False):
            next_page = reverse('marketplace:home')
        elif producto_actual == 'LIBRANZA' or current_flow == ProductAccessProfile.ProductFlow.LIBRANZA:
            next_page = reverse('libranza:landing')
        elif producto_actual == 'EMPRENDIMIENTO' or current_flow == ProductAccessProfile.ProductFlow.EMPRENDIMIENTO:
            next_page = reverse('emprendimiento:landing')
        elif current_flow == ProductAccessProfile.ProductFlow.INVERSIONISTA:
            next_page = reverse('inversionista:login')
        elif current_flow == ProductAccessProfile.ProductFlow.MARKETPLACE_BUYER:
            next_page = reverse('marketplace:home')
        else:
            next_page = reverse('home')

        logout(request)
        request.session.pop('authenticated_product_flow', None)
        request.session.pop('producto_actual', None)
        return redirect(next_page)

