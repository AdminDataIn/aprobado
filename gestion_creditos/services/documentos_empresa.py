from django.core.exceptions import PermissionDenied

from gestion_creditos.models import DocumentoEmpresa


def _exigir_permiso_documentos_empresa(usuario):
    if (
        not usuario
        or not usuario.is_authenticated
        or not usuario.is_staff
        or not usuario.has_perm('gestion_creditos.add_documentoempresa')
    ):
        raise PermissionDenied('No tienes permiso para administrar documentos de empresa.')


def cargar_documento_empresa(
    *,
    empresa,
    tipo_documento,
    archivo,
    usuario,
    fecha_expedicion=None,
    fecha_vencimiento=None,
    observaciones='',
):
    """Crea la versión vigente; el modelo reemplaza la anterior atómicamente."""
    _exigir_permiso_documentos_empresa(usuario)
    return DocumentoEmpresa.objects.create(
        empresa=empresa,
        tipo_documento=tipo_documento,
        archivo=archivo,
        fecha_expedicion=fecha_expedicion,
        fecha_vencimiento=fecha_vencimiento,
        observaciones=(observaciones or '').strip(),
        cargado_por=usuario,
        estado=DocumentoEmpresa.EstadoDocumento.VIGENTE,
        activo=True,
    )
