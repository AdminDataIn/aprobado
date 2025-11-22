from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from configuraciones.models import ConfiguracionPeso
from gestion_creditos.models import Credito, CreditoEmprendimiento
from datetime import datetime
from decimal import Decimal
from django.core.exceptions import ObjectDoesNotExist
import logging
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
logger = logging.getLogger(__name__)
from openai import OpenAI
from django.conf import settings


@csrf_exempt
@require_POST
def recibir_data(request):
    """
    DEPRECADA: Esta función está obsoleta.

    El formulario de solicitud de crédito de emprendimiento ahora usa:
    gestion_creditos.views.solicitud_credito_emprendimiento_view

    Esta función se mantiene temporalmente por compatibilidad, pero redirige
    automáticamente a la nueva implementación que soporta:
    - Múltiples imágenes (en lugar de 1 PDF)
    - Scoring de imágenes con IA
    - Modelo de crédito refactorizado

    TODO: Eliminar esta función después de la migración completa.
    """
    # Redireccionar a la nueva implementación
    from gestion_creditos.views import solicitud_credito_emprendimiento_view
    return solicitud_credito_emprendimiento_view(request)


def evaluar_motivacion_credito(texto):
    """
    Evalúa la motivación para el crédito usando ChatGPT (OpenAI API v1.0.0+).
    Devuelve un puntaje entre 1 y 5.
    """
    if not texto or len(texto) < 10:
        return 3  # Valor por defecto para textos muy cortos

    try:
        # Inicializa el cliente
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        prompt = f"""
        Evalúa esta justificación para un crédito y asigna un puntaje del 1 al 5:
        - 1: Muy pobre (sin explicación clara)
        - 2: Pobre (explicación vaga)
        - 3: Aceptable (propósito básico claro)
        - 4: Bueno (propósito y motivación claros)
        - 5: Excelente (propósito claro con plan detallado)

        Justificación: "{texto}"

        Responde SOLO con el número del puntaje (1-5), nada más.
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Eres un analista financiero experto en evaluar solicitudes de crédito."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2
        )

        # Extraer el puntaje de la respuesta
        respuesta = response.choices[0].message.content.strip()
        puntaje = int(respuesta) if respuesta.isdigit() else 3

        return max(1, min(5, puntaje))  # Asegurarse que esté entre 1 y 5

    except Exception as e:
        print(f"Error al evaluar con ChatGPT: {e}")
        return 0  # Valor por defecto en caso de error


@csrf_exempt
def obtener_estimacion(parametros):
    # Lista para almacenar resultados de las estimaciones
    resultados = []

    # Procesar los parámetros dinámicamente
    for parametro, nivel in parametros.items():
        if nivel:  # Solo procesar si el nivel tiene un valor
            try:
                configuracion = ConfiguracionPeso.objects.get(parametro=parametro, nivel=nivel)
                resultados.append({
                    'parametro': parametro,
                    'nivel': nivel,
                    'estimacion': configuracion.estimacion
                })
            except ObjectDoesNotExist:
                # Manejar casos donde no se encuentra el registro
                print(f"No se encontró configuración para {parametro} con nivel {nivel}")
                resultados.append({
                    'parametro': parametro,
                    'nivel': nivel,
                    'estimacion': 'No disponible'
                })

    # Calcular la suma de las estimaciones
    suma_estimaciones = sum(int(r['estimacion']) for r in resultados if isinstance(r['estimacion'], (int, float, Decimal)))

    # Imprimir para verificar el cálculo
    print("Suma total de estimaciones:", suma_estimaciones)

    return suma_estimaciones