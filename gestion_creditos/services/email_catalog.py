from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone


@dataclass(frozen=True)
class EmailPreviewSpec:
    slug: str
    nombre: str
    categoria: str
    destinatario: str
    flujo: str
    template_html: str
    template_text: str | None
    subject: str
    cta: bool = False
    footer: bool = True
    iconos: bool = True
    notas: str = ''


def _sample_credito():
    fecha_hoy = timezone.now()
    return SimpleNamespace(
        id=101,
        numero_credito='CR-2026-00099',
        monto_solicitado=Decimal('3000000.00'),
        monto_aprobado=Decimal('3000000.00'),
        plazo=6,
        plazo_solicitado=6,
        fecha_solicitud=fecha_hoy,
        fecha_desembolso=fecha_hoy.date(),
        fecha_proximo_pago=date(2026, 5, 30),
        tasa_interes=Decimal('1.90'),
        saldo_pendiente=Decimal('1985750.00'),
        valor_cuota=Decimal('397150.00'),
        total_a_pagar=Decimal('3574350.00'),
        nombre_cliente='Carlos Daniel Ortiz',
        cliente_documento='1122334455',
        usuario=SimpleNamespace(email='cliente@aprobado.test'),
        get_linea_display=lambda: 'Libranza',
    )


def _sample_empresa():
    return SimpleNamespace(
        id=4,
        nombre='FERTOBRA SAS',
        correo_contacto='pagador@fertobra.test',
    )


def _sample_user():
    return SimpleNamespace(
        username='usuario.demo',
        email='demo@aprobado.test',
        first_name='Usuario',
        get_full_name=lambda: 'Usuario Demo',
        get_username=lambda: 'usuario.demo',
    )


def _sample_pagador_profile():
    empresa = _sample_empresa()
    usuario = _sample_user()
    return SimpleNamespace(usuario=usuario, empresa=empresa)


def _sample_asesor():
    usuario = _sample_user()
    return SimpleNamespace(
        nombre='Ejecutivo Demo',
        email='ejecutivo@aprobado.test',
        cedula='1234567890',
        telefono='3001234567',
        usuario=usuario,
    )


def _sample_marketplace_item():
    empresa = _sample_empresa()
    return SimpleNamespace(
        empresa=empresa,
        titulo='Portatil Lenovo ThinkPad',
        get_tipo_display=lambda: 'Producto',
        get_estado_display=lambda: 'Aprobado',
    )


def _sample_marketplace_order():
    empresa = _sample_empresa()
    pedido = SimpleNamespace(
        numero_pedido='PED-2026-00001',
        comprador_nombre='Comprador Demo',
        comprador_email='comprador@aprobado.test',
        comprador_telefono='3009998877',
        total=Decimal('2499000.00'),
        empresa=empresa,
        pago=SimpleNamespace(get_estado_display=lambda: 'Aprobado'),
    )
    items = [
        SimpleNamespace(titulo_snapshot='Portatil Lenovo ThinkPad', cantidad=1, total_linea='2,499,000.00'),
    ]
    direccion = SimpleNamespace(
        direccion_linea_1='Calle 123 #45-67',
        direccion_linea_2='Oficina 301',
        ciudad='Bogota',
        departamento='Cundinamarca',
    )
    return pedido, items, direccion


def _sample_common_context():
    credito = _sample_credito()
    empresa = _sample_empresa()
    user = _sample_user()
    perfil_pagador = _sample_pagador_profile()
    asesor = _sample_asesor()
    marketplace_item = _sample_marketplace_item()
    pedido, order_items, direccion = _sample_marketplace_order()
    reset_url = 'https://aprobado.com.co/accounts/reset/demo-token/'
    dashboard_pagador = 'https://aprobado.com.co/pagador/'
    return {
        'credito': credito,
        'empresa': empresa,
        'usuario': user,
        'display_name': 'Usuario Demo',
        'nombre_cliente': 'Carlos Daniel Ortiz',
        'nuevo_estado': 'Activo',
        'motivo': 'Validacion completa',
        'numero_credito': credito.numero_credito,
        'cedula_solicitante': '1122334455',
        'plazo_solicitado_email': 6,
        'cta_url': 'https://aprobado.com.co/libranza/login/?next=/libranza/mi-credito/',
        'cta_label': 'Consultar estado',
        'cuenta_destino_resumen': 'Banco Demo ****1234',
        'dias_restantes': 3,
        'valor_cuota': '$397.150,00',
        'fecha_vencimiento': date(2026, 5, 30),
        'monto_pagado': '$397.150,00',
        'nuevo_saldo': '$1.985.750,00',
        'fecha_pago': timezone.now(),
        'referencia_pago': 'PAY-2026-0001',
        'metodo_pago': 'Transferencia directa',
        'banco': 'Bancolombia',
        'dias_mora': 12,
        'saldo_pendiente': '$1.985.750,00',
        'detalle': SimpleNamespace(
            nombre_completo='Carlos Daniel Ortiz',
            empresa=empresa,
            cedula='1122334455',
            correo_electronico='cliente@aprobado.test',
            telefono='3001234567',
            celular_wh='3001234567',
        ),
        'admin_url': 'https://aprobado.com.co/gestion/credito/101/',
        'email_cliente': 'cliente@aprobado.test',
        'telefono': '3001234567',
        'titulo': 'Pago offline aplicado',
        'subtitulo': 'Se registró un pago manual y quedó trazabilidad operativa del movimiento.',
        'empresa_nombre': empresa.nombre,
        'referencia': 'REF-0001',
        'fecha_aplicacion': timezone.now(),
        'usuario_nombre': 'Tesoreria Demo',
        'comprobante_nombre': 'comprobante-demo.pdf',
        'credito_url': dashboard_pagador,
        'credito_numero': credito.numero_credito,
        'cliente_nombre': 'Carlos Daniel Ortiz',
        'monto_total': Decimal('1985750.00'),
        'cantidad_registros': 5,
        'origen_label': 'Carga de pagos',
        'notas': 'Notas de referencia para preview.',
        'perfil_pagador': perfil_pagador,
        'activation_url': 'https://aprobado.com.co/pagador/activar/demo-token/',
        'expiration_hours': 24,
        'expires_at': timezone.now() + timezone.timedelta(hours=24),
        'reset_url': 'https://aprobado.com.co/pagador/reset/demo-token/',
        'fecha_corte': date(2026, 4, 30),
        'items': [
            {
                'numero_credito': 'CR-2026-00011',
                'nombre_cliente': 'Orjuela Herrera Dairo Hernan',
                'documento': '1122334455',
                'fecha_vencimiento': date(2026, 4, 30),
                'monto': Decimal('198575.00'),
            },
            {
                'numero_credito': 'CR-2026-00012',
                'nombre_cliente': 'Maria Antonella Suarez',
                'documento': '1006442392',
                'fecha_vencimiento': date(2026, 4, 30),
                'monto': Decimal('238915.48'),
            },
        ],
        'total': Decimal('437490.48'),
        'dashboard_url': dashboard_pagador,
        'restante': Decimal('238915.48'),
        'cuota': SimpleNamespace(
            fecha_vencimiento=date(2026, 4, 30),
        ),
        'asesor': asesor,
        'item': marketplace_item,
        'estado_nuevo': 'APROBADO',
        'estado_nuevo_display': 'Aprobado',
        'comentario': 'La publicación ya cumple los criterios para salir al marketplace.',
        'pedido': pedido,
        'direccion': direccion,
        'direccion_texto': 'Calle 123 #45-67 Oficina 301, Bogota Cundinamarca',
        'estado_pago': 'Aprobado',
        'protocol': 'https',
        'domain': 'aprobado.com.co',
        'uid': 'Mg',
        'uidb64': 'Mg',
        'token': 'set-password-token',
        'reset_route_name': 'password_reset_confirm',
        'producto': 'Aprobado',
        'support_email': 'soporte@aprobado.com.co',
        'support_mailto': 'mailto:soporte@aprobado.com.co',
        'current_year': timezone.now().year,
        'customer_dashboard_url': 'https://aprobado.com.co/libranza/mi-credito/',
        'marketplace_home_url': 'https://market.aprobado.com.co/',
    }


EMAIL_PREVIEW_SPECS = [
    EmailPreviewSpec('credito_en_revision', 'Solicitud recibida', 'usuarios', 'usuario/cliente', 'cambio de estado a EN_REVISION', 'emails/usuarios/credito_en_revision.html', None, 'Preview | Solicitud recibida', True),
    EmailPreviewSpec('credito_rechazado', 'Solicitud no aprobada', 'usuarios', 'usuario/cliente', 'cambio de estado a RECHAZADO', 'emails/usuarios/credito_rechazado.html', None, 'Preview | Solicitud no aprobada', True),
    EmailPreviewSpec('credito_desembolsado', 'Credito desembolsado', 'usuarios', 'usuario/cliente', 'cambio de estado a ACTIVO', 'emails/usuarios/credito_desembolsado.html', None, 'Preview | Crédito desembolsado', True),
    EmailPreviewSpec('credito_en_mora', 'Credito en mora', 'usuarios', 'usuario/cliente', 'cambio de estado a EN_MORA', 'emails/usuarios/credito_en_mora.html', None, 'Preview | Crédito en mora', True),
    EmailPreviewSpec('credito_pagado', 'Credito pagado', 'usuarios', 'usuario/cliente', 'cambio de estado a PAGADO', 'emails/usuarios/credito_pagado.html', None, 'Preview | Crédito pagado', True),
    EmailPreviewSpec('recordatorio_pago', 'Recordatorio de pago', 'usuarios', 'usuario/cliente', 'recordatorio de cuota próxima', 'emails/usuarios/recordatorio_pago.html', None, 'Preview | Recordatorio de pago', True),
    EmailPreviewSpec('confirmacion_pago', 'Confirmacion de pago', 'usuarios', 'usuario/cliente', 'confirmación de pago recibido', 'emails/usuarios/confirmacion_pago.html', None, 'Preview | Confirmación de pago', True),
    EmailPreviewSpec('alerta_mora', 'Alerta de mora', 'usuarios', 'usuario/cliente', 'alerta de mora diaria', 'emails/usuarios/alerta_mora.html', None, 'Preview | Alerta de mora', True),
    EmailPreviewSpec('usuario_alerta_obligacion_pendiente', 'Aviso de cuota pendiente al usuario', 'usuarios', 'usuario/cliente', '10 días después del resumen al pagador', 'emails/usuarios/usuario_alerta_obligacion_pendiente.html', None, 'Preview | Cuota pendiente por regularizar', True, notas='Se envía después del resumen al pagador.'),
    EmailPreviewSpec('pagador_activacion', 'Activacion de pagador', 'pagadores', 'pagador', 'alta o reenvío de activación', 'emails/pagadores/pagador_activacion_cuenta.html', 'emails/pagadores/pagador_activacion_cuenta.txt', 'Preview | Activación de pagador', True),
    EmailPreviewSpec('pagador_reset', 'Reset de pagador', 'pagadores', 'pagador', 'restablecimiento de acceso', 'emails/pagadores/pagador_reset_password.html', 'emails/pagadores/pagador_reset_password.txt', 'Preview | Reset de pagador', True),
    EmailPreviewSpec('pagador_nueva_solicitud', 'Nueva solicitud para validacion del pagador', 'pagadores', 'pagador/convenio', 'registro de nueva solicitud de libranza', 'emails/pagadores/notificacion_solicitud_libranza_empresa.html', None, 'Preview | Nueva solicitud de libranza', True),
    EmailPreviewSpec('pagador_resumen_mensual', 'Resumen mensual de obligaciones', 'pagadores', 'pagador/convenio', 'cierre mensual de cuotas pendientes', 'emails/pagadores/pagador_resumen_cuotas_pendientes.html', None, 'Preview | Resumen mensual de obligaciones', True),
    EmailPreviewSpec('interno_nueva_solicitud', 'Nueva solicitud interna', 'internos', 'operación interna', 'alta de solicitud de crédito', 'emails/internos/notificacion_interna_nueva_solicitud.html', None, 'Preview | Notificación interna de nueva solicitud', True),
    EmailPreviewSpec('operacion_pago_offline', 'Resumen operativo de pago offline', 'internos', 'operación interna', 'registro manual de pago offline', 'emails/operacion/resumen_operativo_pago_offline.html', None, 'Preview | Resumen operativo de pago', True),
    EmailPreviewSpec('ejecutivo_activacion', 'Activacion de ejecutivo', 'ejecutivos', 'ejecutivo', 'alta o reenvío de activación', 'emails/ejecutivos/executive_activation.html', 'emails/ejecutivos/executive_activation.txt', 'Preview | Activación de ejecutivo', True),
    EmailPreviewSpec('inversionista_activacion', 'Activacion de inversionista', 'inversionistas', 'inversionista', 'alta o reenvío de activación', 'emails/inversionistas/investor_activation.html', 'emails/inversionistas/investor_activation.txt', 'Preview | Activación de inversionista', True),
    EmailPreviewSpec('marketplace_welcome', 'Bienvenida marketplace comprador', 'marketplace', 'comprador marketplace', 'registro exitoso en marketplace', 'emails/marketplace/marketplace_welcome.html', 'emails/marketplace/marketplace_welcome.txt', 'Preview | Bienvenida marketplace', True),
    EmailPreviewSpec('marketplace_order_company', 'Nuevo pedido marketplace empresa', 'marketplace', 'empresa marketplace', 'pedido creado', 'emails/marketplace/marketplace_order_company.html', 'emails/marketplace/marketplace_order_company.txt', 'Preview | Pedido marketplace empresa', False),
    EmailPreviewSpec('marketplace_order_customer', 'Confirmacion de pedido marketplace', 'marketplace', 'comprador marketplace', 'pedido creado', 'emails/marketplace/marketplace_order_customer.html', 'emails/marketplace/marketplace_order_customer.txt', 'Preview | Pedido marketplace comprador', False),
    EmailPreviewSpec('marketplace_estado_publicacion', 'Estado de publicacion marketplace', 'marketplace', 'empresa marketplace', 'aprobación/rechazo de publicación', 'emails/marketplace/marketplace_estado_publicacion.html', None, 'Preview | Estado de publicación marketplace', False),
    EmailPreviewSpec('customer_password_reset', 'Reset de cliente general', 'usuarios', 'usuario/cliente', 'password reset general', 'account/common/customer_password_reset_email.html', 'account/common/customer_password_reset_email.txt', 'Preview | Reset de cliente', True),
    EmailPreviewSpec('inversionista_password_reset', 'Reset de inversionista', 'inversionistas', 'inversionista', 'password reset inversionista', 'account/inversionista/password_reset_email.html', 'account/inversionista/password_reset_email.txt', 'Preview | Reset de inversionista', True),
    EmailPreviewSpec('marketplace_password_reset', 'Reset de comprador marketplace', 'marketplace', 'comprador marketplace', 'password reset marketplace buyer', 'account/common/marketplace_password_reset_email.html', 'account/common/marketplace_password_reset_email.txt', 'Preview | Reset marketplace comprador', True),
    EmailPreviewSpec('marketplace_admin_password_reset', 'Reset de administrador marketplace', 'marketplace', 'admin marketplace', 'password reset marketplace admin', 'account/marketplace_admin/password_reset_email.html', 'account/marketplace_admin/password_reset_email.txt', 'Preview | Reset marketplace administrador', True),
]


def get_email_preview_specs():
    return list(EMAIL_PREVIEW_SPECS)


def build_email_preview_context(spec_slug):
    context = _sample_common_context()
    if spec_slug == 'customer_password_reset':
        context['reset_route_name'] = 'libranza:password_reset_confirm'
        context['producto'] = 'Libranza'
        context['reset_url'] = 'https://aprobado.com.co/libranza/reset/demo-token/'
    elif spec_slug == 'inversionista_password_reset':
        context['reset_route_name'] = 'inversionista:password_reset_confirm'
        context['producto'] = 'Inversionista'
        context['reset_url'] = 'https://aprobado.com.co/inversionista/reset/demo-token/'
    elif spec_slug == 'marketplace_password_reset':
        context['reset_url'] = 'https://market.aprobado.com.co/marketplace/reset/demo-token/'
    elif spec_slug == 'marketplace_admin_password_reset':
        context['reset_url'] = 'https://market.aprobado.com.co/marketplace/panel/reset/demo-token/'
    return context


def build_email_inventory():
    inventory = []
    for spec in get_email_preview_specs():
        inventory.append({
            'slug': spec.slug,
            'nombre': spec.nombre,
            'categoria': spec.categoria,
            'destinatario': spec.destinatario,
            'flujo': spec.flujo,
            'template_html': spec.template_html,
            'template_text': spec.template_text,
            'footer': spec.footer,
            'cta': spec.cta,
            'iconos': spec.iconos,
            'notas': spec.notas,
        })
    return inventory


def render_preview_payload(spec: EmailPreviewSpec):
    context = build_email_preview_context(spec.slug)
    html_content = render_to_string(spec.template_html, context)
    text_content = ''
    if spec.template_text:
        text_content = render_to_string(spec.template_text, context)
    return {
        'subject': spec.subject,
        'html': html_content,
        'text': text_content,
        'context': context,
    }


def send_email_previews(*, to_email, only=None, prefix='[Preview]'):
    sent = []
    only = set(only or [])
    for spec in get_email_preview_specs():
        if only and spec.slug not in only:
            continue
        payload = render_preview_payload(spec)
        email = EmailMultiAlternatives(
            subject=f"{prefix} {payload['subject']}",
            body=payload['text'] or f"Preview de {spec.nombre}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
        )
        email.attach_alternative(payload['html'], 'text/html')
        email.send(fail_silently=False)
        sent.append(spec.slug)
    return sent
