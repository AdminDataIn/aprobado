from io import BytesIO
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from gestion_creditos.services import captura_documental as servicio


def imagen_documental(color='red', nombre='documento.png'):
    buffer = BytesIO()
    Image.new('RGB', (80, 60), color).save(buffer, 'PNG')
    return SimpleUploadedFile(nombre, buffer.getvalue(), content_type='image/png')


def sesion_finalizada(usuario, producto='LIBRANZA', solicitud=None):
    params = {'actor': usuario, 'producto': producto, 'solicitud_id': solicitud.pk if solicitud else None}
    sesion, token = servicio.crear_sesion(**params)
    params['sesion_id'] = sesion.pk
    servicio.canjear_sesion(**params, token=token, vinculo='navegador-test')
    for lado, color in [('FRONTAL', 'red'), ('TRASERA', 'blue')]:
        servicio.recibir_captura(**params, vinculo='navegador-test', lado=lado, archivo=imagen_documental(color))
    return servicio.finalizar_sesion(**params, vinculo='navegador-test')
