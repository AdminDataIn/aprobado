import requests
import json
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class ImageScoringClient:
    def __init__(self):
        # Puerto 8001 por pruebas locales
        self.api_url = getattr(settings, 'SCORING_API_URL', 'http://localhost:8001/api/scoring/score_images/')

    def enviar_imagenes_para_scoring(self, imagenes, tipos_imagen, descripcion_negocio=""):
        """
        Envía imágenes a la API de scoring para análisis con IA.

        Args:
            imagenes (list): Lista de archivos de imagen
            tipos_imagen (list): Lista de tipos declarados para cada imagen
            descripcion_negocio (str): Descripción del negocio para contexto

        Returns:
            dict: Resultado del scoring con puntaje y detalles
        """
        try:
            files = []
            data = {}

            fast_api_url = self.api_url.replace('score_images', 'score_images_fast')
            print(f"Usando endpoint RÁPIDO: {fast_api_url}")

            print(f"Enviando {len(imagenes)} imágenes para scoring...")
            print(f"Tipos declarados: {tipos_imagen}")
            print(f"Descripción: {descripcion_negocio}")

            # Validar la misma cantidad de tipos de imagenes
            if len(tipos_imagen) != len(imagenes):
                print(f"Ajustando tipos: {len(tipos_imagen)} → {len(imagenes)}")
                if len(tipos_imagen) > 0:
                    # Repetir el último tipo disponible
                    tipos_imagen = tipos_imagen + [tipos_imagen[-1]] * (len(imagenes) - len(tipos_imagen))
                else:
                    tipos_imagen = ['general'] * len(imagenes)

            # Preparar datos
            for i, (imagen, tipo_imagen) in enumerate(zip(imagenes, tipos_imagen)):
                # Agregar archivos
                files.append(('images', (
                    imagen.name,
                    imagen,
                    imagen.content_type or 'image/jpeg'
                )))

                # En lugar de sobreescribir, crear múltiples entradas con el mismo nombre
                if 'image_types' not in data:
                    data['image_types'] = []
                data['image_types'].append(tipo_imagen)

            # Agregar descripción del negocio
            if descripcion_negocio:
                data['business_description'] = descripcion_negocio

            print(f"Datos a enviar - Images: {len(files)}, Image_types: {len(data.get('image_types', []))}")
            print(f"Tipos finales: {data.get('image_types', [])}")

            # ENVIAR CON LA ESTRUCTURA CORRECTA
            response = requests.post(
                self.api_url,
                files=files,
                data=data,
                timeout=180
            )

            print(f"Response status: {response.status_code}")

            if response.status_code == 200:
                resultado = response.json()
                puntaje = resultado.get('puntaje', 9.0)
                correspondence_verified = resultado.get('correspondence_verified', True)
                issues_count = resultado.get('correspondence_issues_count', 0)

                print(f"Scoring exitoso - Puntaje: {puntaje}/18")
                print(f"Correspondencia: {correspondence_verified} (issues: {issues_count})")

                return {
                    'success': True,
                    'puntaje': puntaje,
                    'correspondence_verified': correspondence_verified,
                    'correspondence_issues_count': issues_count,
                    'data': resultado
                }
            else:
                print(f"Error {response.status_code}: {response.text}")
                return {
                    'success': False,
                    'error': f"Error {response.status_code}",
                    'puntaje': 9.0,
                    'details': response.text
                }

        except Exception as e:
            print(f"Error en scoring client: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'puntaje': 9.0
            }

scoring_client = ImageScoringClient()
