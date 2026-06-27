from django.conf import settings
from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorBranding,
    ContractorOrganization,
    ContractorProductConfig,
    ContractorProfile,
    ConfiguracionPortalContratistas,
    InformacionLaboralSolicitudContratista,
)
from django.db.models import Q
from gestion_creditos.models import Credito, Empresa


def obtener_organizacion_por_subdominio(subdominio):
    subdominio = (subdominio or '').strip().lower()
    if not subdominio:
        return None
    return ContractorOrganization.objects.filter(
        subdomain=subdominio,
        is_active=True,
    ).first()


def obtener_configuracion_portal_contratistas_por_host(host):
    host_recibido = _normalizar_host_recibido(host)
    if not host_recibido:
        return None

    candidatos = []
    host_sin_puerto = ConfiguracionPortalContratistas.normalizar_host(host_recibido)
    for candidato in (host_recibido, host_sin_puerto):
        if candidato and candidato not in candidatos:
            candidatos.append(candidato)

    for candidato in candidatos:
        configuracion = ConfiguracionPortalContratistas.objects.filter(
            host=candidato,
            activo=True,
        ).first()
        if configuracion:
            return configuracion

    if getattr(settings, 'DEBUG', False):
        configuracion_slug = obtener_configuracion_portal_contratistas_por_slug('contratistas')
        if configuracion_slug:
            return configuracion_slug

        activas = list(ConfiguracionPortalContratistas.objects.filter(activo=True).order_by('id')[:2])
        if len(activas) == 1:
            return activas[0]

    return None


def listar_hosts_configuracion_portal_contratistas_activos():
    return list(
        ConfiguracionPortalContratistas.objects
        .filter(activo=True)
        .order_by('host')
        .values_list('host', flat=True)
    )


def _normalizar_host_recibido(host):
    host = (host or '').strip().lower()
    host = host.removeprefix('https://').removeprefix('http://')
    return host.split('/', 1)[0]


def obtener_configuracion_portal_contratistas_por_slug(slug):
    slug = (slug or '').strip().lower()
    if not slug:
        return None
    return ConfiguracionPortalContratistas.objects.filter(
        slug=slug,
        activo=True,
    ).first()


def obtener_configuracion_producto_activa(organizacion, tipo_producto):
    if not organizacion:
        return None
    return ContractorProductConfig.objects.filter(
        organization=organizacion,
        product_type=tipo_producto,
        is_active=True,
    ).first()


def obtener_branding_activo_por_organizacion(organizacion):
    if not organizacion:
        return None
    return ContractorBranding.objects.filter(
        organization=organizacion,
        is_active=True,
        organization__is_active=True,
    ).first()


def obtener_perfil_contratista_usuario(usuario, organizacion=None):
    if not getattr(usuario, 'is_authenticated', False):
        return None

    queryset = ContractorProfile.objects.select_related('organization').filter(
        user=usuario,
        is_active=True,
        organization__is_active=True,
    )
    if organizacion is not None:
        queryset = queryset.filter(organization=organizacion)
    return queryset.order_by('id').first()


def usuario_pertenece_a_organizacion(usuario, organizacion):
    if not organizacion:
        return False
    return obtener_perfil_contratista_usuario(usuario, organizacion=organizacion) is not None


def obtener_solicitud_contratista(solicitud_id, organizacion=None, configuracion_portal=None, usuario=None):
    queryset = ContractorApplication.objects.select_related(
        'organization',
        'configuracion_portal',
        'product_config',
        'credito',
    )
    if organizacion is not None:
        queryset = queryset.filter(organization=organizacion)
    if configuracion_portal is not None:
        queryset = queryset.filter(configuracion_portal=configuracion_portal)
    if usuario is not None:
        queryset = queryset.filter(usuario=usuario)
    return queryset.filter(id=solicitud_id).first()


def listar_solicitudes_por_organizacion(organizacion):
    if not organizacion:
        return ContractorApplication.objects.none()
    return (
        ContractorApplication.objects
        .select_related('organization', 'product_config', 'credito')
        .filter(organization=organizacion)
        .order_by('-created_at')
    )


def listar_documentos_solicitud_contratista(solicitud):
    if not solicitud:
        return ContractorApplicationDocument.objects.none()
    return (
        ContractorApplicationDocument.objects
        .select_related('application', 'application__organization', 'reviewed_by')
        .filter(application=solicitud)
        .order_by('-uploaded_at')
    )


def solicitud_tiene_documento_tipo(solicitud, tipo_documento):
    if not solicitud or not tipo_documento:
        return False
    return ContractorApplicationDocument.objects.filter(
        application=solicitud,
        document_type=tipo_documento,
    ).exists()


def obtener_ultimo_documento_por_tipo(solicitud, tipo_documento):
    if not solicitud or not tipo_documento:
        return None
    return (
        ContractorApplicationDocument.objects
        .select_related('application', 'application__organization', 'reviewed_by')
        .filter(application=solicitud, document_type=tipo_documento)
        .order_by('-uploaded_at', '-id')
        .first()
    )


def obtener_datos_contractuales_solicitud(solicitud):
    if not solicitud:
        return None
    return (
        InformacionLaboralSolicitudContratista.objects
        .select_related('solicitud', 'solicitud__organization', 'empresa')
        .filter(solicitud=solicitud)
        .first()
    )


def listar_empresas_libranza_convenio_activas():
    return (
        Empresa.objects
        .filter(convenio_activo=True)
        .exclude(tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA)
        .order_by('nombre')
    )


def solicitud_tiene_datos_contractuales(solicitud):
    if not solicitud:
        return False
    return InformacionLaboralSolicitudContratista.objects.filter(solicitud=solicitud).exists()


def obtener_credito_previo_por_documento_solicitud(solicitud):
    if not solicitud or not solicitud.document_number:
        return None

    documento = str(solicitud.document_number).strip()
    if not documento:
        return None

    return (
        Credito.objects
        .select_related(
            'usuario',
            'detalle_libranza',
            'detalle_adelanto_nomina',
            'detalle_adelanto_nomina__vinculo_laboral',
            'detalle_emprendimiento',
        )
        .filter(
            Q(detalle_libranza__cedula=documento)
            | Q(detalle_adelanto_nomina__vinculo_laboral__documento_empleado=documento)
            | Q(detalle_emprendimiento__numero_cedula=documento),
        )
        .order_by('-fecha_solicitud', '-id')
        .first()
    )


# Aliases temporales de compatibilidad.
get_organization_by_subdomain = obtener_organizacion_por_subdominio
get_active_product_config = obtener_configuracion_producto_activa
get_active_branding_by_organization = obtener_branding_activo_por_organizacion
get_user_contractor_profile = obtener_perfil_contratista_usuario
user_belongs_to_organization = usuario_pertenece_a_organizacion
