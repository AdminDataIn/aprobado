from django.shortcuts import render, get_object_or_404 # type: ignore
from django.contrib.auth.decorators import login_required # type: ignore
from django.http import JsonResponse # type: ignore
import datetime
from django.template.loader import render_to_string # type: ignore
from django.http import HttpResponse # type: ignore
from weasyprint import HTML # type: ignore
import os
from django.conf import settings # type: ignore
import tempfile
import pathlib

# @login_required
def dashboard_view(request, credito_id=None):
    # Obtener todos los créditos del usuario
    creditos_usuario = obtener_creditos_usuario(request.user if hasattr(request, 'user') else None)
    
    # Si no se especifica crédito_id, usar el más reciente activo
    if credito_id is None:
        credito_activo = next((c for c in creditos_usuario if c['estado'] in ['Al día', 'En mora']), creditos_usuario[0] if creditos_usuario else None)
    else:
        credito_activo = next((c for c in creditos_usuario if c['id'] == int(credito_id)), None)
    
    if not credito_activo:
        # Manejar caso sin créditos
        return render(request, 'usuariocreditos/sin_creditos.html')
    
    # Datos del crédito seleccionado
    monto_total_credito = credito_activo['monto_total']
    monto_aprobado = credito_activo['monto_aprobado']
    monto_pagado = credito_activo['monto_pagado']
    cuota_pendiente = credito_activo['cuota_pendiente']
    
    # Cálculos para la barra de progreso
    monto_pendiente = monto_total_credito - monto_pagado
    porcentaje_pagado = round((monto_pagado / monto_total_credito) * 100) if monto_total_credito > 0 else 0
    
    # Lógica de incentivos
    pagos_puntuales = credito_activo['pagos_puntuales']
    mostrar_incentivo = porcentaje_pagado >= 25 and pagos_puntuales
    mostrar_seccion_incentivos = porcentaje_pagado >= 20 and pagos_puntuales
    
    # Mensajes dinámicos según el rendimiento
    if porcentaje_pagado >= 50:
        mensaje_incentivos = "¡Excelente! Has desbloqueado asesorías gratuitas y descuentos especiales."
    elif porcentaje_pagado >= 30:
        mensaje_incentivos = "¡Felicidades! Tu puntualidad te ha desbloqueado beneficios especiales."
    elif porcentaje_pagado >= 20:
        mensaje_incentivos = "Continúa así para desbloquear increíbles beneficios."
    else:
        mensaje_incentivos = "Mantén tus pagos al día para acceder a incentivos exclusivos."
    
    # Estado crediticio
    estado_credito = credito_activo['estado']
    
    if estado_credito == 'Al día':
        estado_credito_class = 'success'
        mensaje_estado_credito = 'Excelente historial crediticio' if pagos_puntuales else 'Buen historial crediticio'
    elif estado_credito == 'En mora':
        estado_credito_class = 'danger'
        mensaje_estado_credito = 'Regulariza tu situación'
    elif estado_credito == 'Completado':
        estado_credito_class = 'success'
        mensaje_estado_credito = 'Crédito completado exitosamente'
    else:
        estado_credito_class = 'secondary'
        mensaje_estado_credito = estado_credito
    
    # Categorizar créditos para el selector
    creditos_activos = [c for c in creditos_usuario if c['estado'] in ['Al día', 'En mora']]
    creditos_completados = [c for c in creditos_usuario if c['estado'] == 'Completado']
    creditos_rechazados = [c for c in creditos_usuario if c['estado'] == 'Rechazado']
    
    context = {
        'nombre_asociado': 'Usuario de Prueba',
        'monto_aprobado': monto_aprobado,
        'cuota_pendiente': cuota_pendiente,
        'proximo_vencimiento': credito_activo['proximo_vencimiento'],
        'estado_credito': estado_credito,
        'historial_pagos': credito_activo['historial_pagos'],
        'historial_solicitudes': obtener_historial_solicitudes_usuario(),
        
        # Variables para la barra de progreso
        'porcentaje_pagado': porcentaje_pagado,
        'monto_pagado': monto_pagado,
        'monto_pendiente': monto_pendiente,
        'monto_total': monto_total_credito,
        
        # Variables para el sistema de incentivos
        'mostrar_incentivo': mostrar_incentivo,
        'mostrar_seccion_incentivos': mostrar_seccion_incentivos,
        'mensaje_incentivos': mensaje_incentivos,
        
        # Variables para el estado crediticio
        'estado_credito_class': estado_credito_class,
        'mensaje_estado_credito': mensaje_estado_credito,
        
        # Nuevas variables para múltiples créditos
        'credito_actual': credito_activo,
        'creditos_activos': creditos_activos,
        'creditos_completados': creditos_completados,
        'creditos_rechazados': creditos_rechazados,
        'total_creditos': len(creditos_usuario),
        'tiene_multiples_creditos': len(creditos_usuario) > 1,
    }
    
    return render(request, 'usuariocreditos/dashboard.html', context)

def obtener_creditos_usuario(user=None):
    """Función para obtener todos los créditos del usuario"""
    # Datos de ejemplo - reemplazar con consulta real a la base de datos
    return [
        {
            'id': 1,
            'numero_credito': 'CR-2024-001',
            'monto_total': 800000,
            'monto_aprobado': 800000,
            'monto_pagado': 636160,
            'cuota_pendiente': 318080,
            'estado': 'Al día',
            'pagos_puntuales': True,
            'fecha_inicio': datetime.date(2024, 5, 10),
            'proximo_vencimiento': datetime.date.today() + datetime.timedelta(days=18),
            'historial_pagos': [
                {'fecha': datetime.datetime(2025, 7, 22, 10, 30, 0), 'monto': 318080, 'referencia': '#AB789-07', 'estado': 'En Mora (Pagado)'},
                {'fecha': datetime.datetime(2025, 6, 18, 15, 0, 0), 'monto': 318080, 'referencia': '#AB789-04', 'estado': 'Completado'},
            ],
            'tipo': 'Nanocredito'
        },
        {
            'id': 2,
            'numero_credito': 'CR-2023-045',
            'monto_total': 300000,
            'monto_aprobado': 300000,
            'monto_pagado': 300000,
            'cuota_pendiente': 0,
            'estado': 'Completado',
            'pagos_puntuales': True,
            'fecha_inicio': datetime.date(2023, 9, 20),
            'proximo_vencimiento': None,
            'historial_pagos': [
                {'fecha': datetime.datetime(2024, 3, 15, 9, 0, 0), 'monto': 100000, 'referencia': '#CR023-12', 'estado': 'Completado'},
                {'fecha': datetime.datetime(2024, 2, 15, 11, 20, 0), 'monto': 100000, 'referencia': '#CR023-11', 'estado': 'Completado'},
                {'fecha': datetime.datetime(2024, 1, 15, 14, 45, 0), 'monto': 100000, 'referencia': '#CR023-10', 'estado': 'Completado'},
            ],
            'tipo': 'Nanocredito'
        },
        { 
            'id': 3,
            'numero_credito': 'CR-2024-078',
            'monto_total': 200000,
            'monto_aprobado': 200000,
            'monto_pagado': 79520,
            'cuota_pendiente': 159040,
            'estado': 'En mora',
            'pagos_puntuales': False,
            'fecha_inicio': datetime.date(2024, 8, 15),
            'proximo_vencimiento': datetime.date.today() - datetime.timedelta(days=5),
            'historial_pagos': [
                {'fecha': datetime.datetime(2024, 12, 10, 18, 0, 0), 'monto': 79520, 'referencia': '#CR078-01', 'estado': 'Completado'},
            ],
            'tipo': 'Nanocredito'
        }
    ]

def obtener_historial_solicitudes_usuario():
    """Función para obtener el historial completo de solicitudes"""
    return [
        {'fecha': datetime.date(2024, 5, 10), 'monto': 800000, 'estado': 'Aprobado', 'tipo': 'Nanocredito'},
        {'fecha': datetime.date(2024, 1, 15), 'monto': 300000, 'estado': 'Rechazado', 'tipo': 'Nanocredito'},
        {'fecha': datetime.date(2023, 9, 20), 'monto': 300000, 'estado': 'Aprobado', 'tipo': 'Nanocredito'},
    ]

# Vista AJAX para cambiar de crédito
def cambiar_credito(request, credito_id):
    """Vista para cambiar entre créditos via AJAX"""
    if request.method == 'GET':
        return dashboard_view(request, credito_id)
    else:
        return JsonResponse({'error': 'Método no permitido'}, status=405)

def extracto_pdf_view(request, credito_id):
    """Genera y devuelve el extracto en PDF para el crédito especificado"""
    # 1. Obtener los datos del crédito
    creditos = obtener_creditos_usuario(request.user if hasattr(request, 'user') else None)
    credito = next((c for c in creditos if c['id'] == int(credito_id)), None)

    if not credito:
        return HttpResponse("Crédito no encontrado", status=404)

    # 2. Calcular valores adicionales
    monto_pendiente = credito['monto_total'] - credito['monto_pagado']

    # 3. Preparar el contexto para la plantilla
    logo_path = os.path.join(settings.STATICFILES_DIRS[0], 'images', 'logo-dark.png')
    logo_uri = pathlib.Path(logo_path).as_uri()

    context = {
        'usuario': {
            'get_full_name': 'Maria Jose',
            'direccion': 'Calle 32 # 29, Villavicencio, Colombia',
            'id_cliente': '123456789'
        },
        'credito': credito,
        'pagos': credito['historial_pagos'],
        'logo_path': logo_uri,
        'fecha_extracto': datetime.datetime.now(),
        'numero_extracto': f'EXT-00{credito["id"]}-2025',
        'monto_pendiente': monto_pendiente,
    }

    # 4. Renderizar la plantilla HTML a un string
    html_string = render_to_string('usuariocreditos/extracto_pdf.html', context)

    # 5. Generar el PDF con WeasyPrint
    html = HTML(string=html_string, base_url=request.build_absolute_uri('/'))
    pdf_file = html.write_pdf()

    # 6. Crear y devolver la respuesta HTTP con el PDF
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="extracto_{credito["numero_credito"]}.pdf"'
    
    return response