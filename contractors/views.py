from django.contrib.auth import logout
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from django.conf import settings
from django.http import Http404
from django.http import JsonResponse
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import set_urlconf
from django.views.decorators.http import require_POST

from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.utils import timezone

from contractors.forms import (
    CAMPOS_HASH_ANALISIS_CONTRACTUAL,
    calcular_hash_analisis_contractual_desde_datos,
    FormularioDocumentoSolicitudContratista,
    FormularioSimulacionContratista,
    FormularioSolicitudContratista,
)
from contractors.models import (
    ContractorApplicationDocument,
    TAMANO_MAXIMO_DOCUMENTO_BYTES,
)
from contractors.selectors import (
    listar_hosts_configuracion_portal_contratistas_activos,
    listar_documentos_solicitud_contratista,
    obtener_solicitud_contratista,
)
from contractors.services.branding import obtener_contexto_branding_con_defaults
from contractors.services.documentos import (
    DatosDocumentoSolicitudContratista,
    registrar_documento_solicitud_contratista,
)
from contractors.services.datos_contractuales import (
    DatosContractualesContratista,
    ErrorDatosContractualesContratista,
    registrar_datos_contractuales_contratista,
)
from contractors.services.analisis_contrato_ia import (
    analizar_contrato_con_openai,
)
from contractors.services.analisis_contractual_seguro import (
    enriquecer_analisis_contrato_prestador,
)
from contractors.services.autorizacion_datacredito import (
    ErrorAutorizacionDatacredito,
    registrar_autorizacion_datacredito_prestador,
)
from contractors.services.simulation import (
    ErrorSimulacionContratista,
    aceptar_simulacion_prestador,
    simular_credito_portal_contratistas,
    validar_solicitud_lista_para_simular,
)
from contractors.services.solicitudes import DatosSolicitudContratista, crear_solicitud_contratista
from contractors.services.timeline import registrar_evento_timeline_prestador
from contractors.models import AutorizacionConsultaDatacreditoPrestador
from gestion_creditos.models import Empresa
from usuariocreditos.views import dashboard_libranza_view
from usuarios.models import ProductAccessProfile
from usuarios.views import ProductLoginView, ProductRegisterView


class VistaLoginContratistas(ProductLoginView):
    template_name = 'account/libranza/login.html'
    next_default_url = '/solicitar/'
    target_flow = ProductAccessProfile.ProductFlow.LIBRANZA
    registration_url_name = 'contractors:registro_contratistas'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'producto_auth_nombre': 'Prestadores de Servicios Aprobado',
                'badge_auth_texto': 'Acceso Prestadores de Servicios',
                'descripcion_auth_texto': (
                    'Accede con tu correo o continua con Google para registrar tu informacion '
                    'contractual y cargar documentos.'
                ),
                'back_url': '/',
                'volver_auth_texto': 'Volver a Prestadores de Servicios',
                'ocultar_recuperar_password': True,
            },
        )
        return context


class VistaRegistroContratistas(ProductRegisterView):
    template_name = 'account/libranza/register.html'
    next_default_url = '/solicitar/'
    target_flow = ProductAccessProfile.ProductFlow.LIBRANZA
    login_url_name = 'contractors:login_contratistas'
    landing_url_name = 'contractors:landing_contratista'

    def _build_context(self, form):
        context = super()._build_context(form)
        context.update(self._contexto_auth_contratistas())
        return context

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._contexto_auth_contratistas())
        return context

    @staticmethod
    def _contexto_auth_contratistas():
        return {
            'producto_auth_nombre': 'Prestadores de Servicios Aprobado',
            'badge_auth_texto': 'Nueva cuenta Prestadores de Servicios',
            'descripcion_registro_texto': (
                'Crea tu acceso para registrar informacion contractual, seleccionar empresa '
                'y cargar documentos.'
            ),
            'volver_auth_texto': 'Volver a Prestadores de Servicios',
        }


@require_POST
def logout_contratistas_view(request):
    logout(request)
    return redirect('contractors:login_contratistas')


def landing_contratista_view(request):
    _obtener_configuracion_portal_activa(request)
    return redirect('contractors:solicitud_contratista')


@login_required(login_url='/login/')
def mi_credito_contratista_view(request):
    _obtener_configuracion_portal_activa(request)
    request.urlconf = 'aprobado_web.urls_main'
    set_urlconf('aprobado_web.urls_main')
    try:
        return dashboard_libranza_view(request)
    finally:
        set_urlconf(None)


@login_required(login_url='/login/')
def simulador_contratista_view(request):
    configuracion_portal = _obtener_configuracion_portal_activa(request)
    solicitud_id = request.GET.get('solicitud_id')
    if not solicitud_id:
        return redirect('contractors:solicitud_contratista')

    solicitud = obtener_solicitud_contratista(
        solicitud_id,
        configuracion_portal=configuracion_portal,
        usuario=request.user,
    )
    if not solicitud:
        raise Http404('solicitud_contratista_no_encontrada')

    branding = obtener_contexto_branding_con_defaults(configuracion_portal)
    resultado = None
    simulacion_aceptada = False
    simulacion_guardada = None

    if request.method == 'POST':
        formulario = FormularioSimulacionContratista(request.POST)
        if formulario.is_valid():
            try:
                simulacion_guardada = aceptar_simulacion_prestador(
                    solicitud=solicitud,
                    monto=formulario.cleaned_data['monto'],
                    plazo_meses=formulario.cleaned_data['plazo_meses'],
                    usuario=request.user,
                    request=request,
                )
                simulacion_aceptada = True
                resultado = simular_credito_portal_contratistas(
                    configuracion_portal=configuracion_portal,
                    monto=simulacion_guardada.monto_solicitado,
                    plazo_meses=simulacion_guardada.plazo_meses,
                )
            except ErrorSimulacionContratista as exc:
                formulario.add_error(None, _mensajes_validacion(exc))
    else:
        monto_inicial = solicitud.requested_amount or configuracion_portal.min_amount
        plazo_inicial = solicitud.term_months or configuracion_portal.min_term_months
        formulario = FormularioSimulacionContratista(
            initial={
                'monto': monto_inicial,
                'plazo_meses': plazo_inicial,
            },
        )
        try:
            validar_solicitud_lista_para_simular(solicitud)
        except ErrorSimulacionContratista as exc:
            formulario.add_error(None, _mensajes_validacion(exc))

    return render(
        request,
        'contractors/simulador_contratista.html',
        {
            'branding': branding,
            'configuracion_portal': configuracion_portal,
            'configuracion_producto': configuracion_portal,
            'formulario': formulario,
            'organizacion': None,
            'resultado': resultado,
            'solicitud': solicitud,
            'simulacion_aceptada': simulacion_aceptada,
            'simulacion_guardada': simulacion_guardada,
        },
    )


@login_required(login_url='/login/')
@require_POST
def calcular_simulacion_contratista_view(request):
    configuracion_portal = _obtener_configuracion_portal_activa(request)
    try:
        payload = json.loads((request.body or b'{}').decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'payload_invalido'}, status=400)

    solicitud = obtener_solicitud_contratista(
        payload.get('solicitud_id'),
        configuracion_portal=configuracion_portal,
        usuario=request.user,
    )
    if not solicitud:
        raise Http404('solicitud_contratista_no_encontrada')

    monto = payload.get('monto')
    plazo_meses = payload.get('plazo_meses')
    llave_rate_limit = f'simulacion-prestador:{request.user.pk}:{solicitud.pk}:{monto}:{plazo_meses}'
    if cache.get(llave_rate_limit):
        return JsonResponse({'ok': False, 'error': 'solicitud_repetida'}, status=429)
    cache.set(llave_rate_limit, True, timeout=1)

    try:
        validar_solicitud_lista_para_simular(solicitud)
        resultado = simular_credito_portal_contratistas(
            configuracion_portal=configuracion_portal,
            monto=monto,
            plazo_meses=plazo_meses,
        )
    except (ErrorSimulacionContratista, ValueError, TypeError) as exc:
        return JsonResponse({'ok': False, 'error': _mensajes_validacion(exc)}, status=400)

    return JsonResponse({'ok': True, 'resultado': _resultado_simulacion_json(resultado)})


@login_required(login_url='/login/')
def solicitud_contratista_view(request):
    configuracion_portal = _obtener_configuracion_portal_activa(request)

    branding = obtener_contexto_branding_con_defaults(configuracion_portal)

    if request.method == 'POST':
        formulario = FormularioSolicitudContratista(
            request.POST,
            request.FILES,
            configuracion_producto=configuracion_portal,
        )
        if formulario.is_valid():
            try:
                payload_simulacion = {
                    'simulacion_pendiente': True,
                    'analisis_contrato_ia': {
                        'enabled': False,
                        'attempted': False,
                        'success': False,
                        'error_tipo': 'analisis_en_endpoint_independiente',
                    },
                    'analisis_contractual_seguro': formulario.cleaned_data.get('analisis_contractual_metadata') or {},
                }
                datos = DatosSolicitudContratista(
                    tipo_documento=formulario.cleaned_data['tipo_documento'],
                    numero_documento=formulario.cleaned_data['numero_documento'],
                    nombres=formulario.cleaned_data['nombres'],
                    apellidos=formulario.cleaned_data['apellidos'],
                    celular=formulario.cleaned_data['celular'],
                    correo=formulario.cleaned_data['correo'],
                    escenario_credito=formulario.cleaned_data['escenario_credito'],
                    direccion=formulario.cleaned_data['direccion'],
                    terminos_aceptados=formulario.cleaned_data['terminos_aceptados'],
                    cuota_mensual_estimada=0,
                    payload_simulacion=payload_simulacion,
                    subdominio_origen=configuracion_portal.slug,
                    ip_address=_obtener_ip_cliente(request),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                )
                datos_contractuales = DatosContractualesContratista(
                    cargo=formulario.cleaned_data['cargo'],
                    tipo_contrato=formulario.cleaned_data['tipo_contrato'],
                    empresa=formulario.cleaned_data['empresa'],
                    fecha_inicio_contrato=formulario.cleaned_data['fecha_inicio_contrato'],
                    fecha_fin_contrato=formulario.cleaned_data['fecha_fin_contrato'],
                    valor_total_contrato=formulario.cleaned_data['valor_total_contrato'],
                    valor_pagado_contrato=formulario.cleaned_data['valor_pagado_contrato'],
                    valor_pendiente_cobrar=formulario.cleaned_data['valor_pendiente_cobrar'],
                    observaciones=formulario.cleaned_data['observaciones'],
                )
                with transaction.atomic():
                    resultado_solicitud = crear_solicitud_contratista(
                        configuracion_portal=configuracion_portal,
                        datos=datos,
                        usuario=request.user,
                    )
                    if formulario.cleaned_data.get('autorizacion_datacredito_aceptada'):
                        registrar_autorizacion_datacredito_prestador(
                            solicitud=resultado_solicitud.solicitud,
                            usuario=request.user,
                            source=AutorizacionConsultaDatacreditoPrestador.Fuente.FORMULARIO_PUBLICO,
                            request=request,
                        )
                    registrar_datos_contractuales_contratista(
                        solicitud=resultado_solicitud.solicitud,
                        datos=datos_contractuales,
                    )
                    documentos_registrados = _registrar_documentos_iniciales(
                        solicitud=resultado_solicitud.solicitud,
                        formulario=formulario,
                    )
                    contrato_registrado = documentos_registrados.get(
                        ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
                    )
                    if contrato_registrado:
                        resultado_solicitud.solicitud.simulation_payload['contrato_documento_id'] = (
                            contrato_registrado.documento_id
                        )
                        resultado_solicitud.solicitud.save(update_fields=['simulation_payload', 'updated_at'])
                return redirect(f'/simular/?solicitud_id={resultado_solicitud.solicitud_id}')
            except (
                ErrorSimulacionContratista,
                ErrorDatosContractualesContratista,
                ErrorAutorizacionDatacredito,
                ValidationError,
            ) as exc:
                formulario.add_error(None, _mensajes_validacion(exc))
        else:
            _registrar_huella_analisis_contractual_obsoleto(request, formulario)
            _registrar_huella_empresa_contrato_no_coincide(request, formulario)
    else:
        formulario = FormularioSolicitudContratista(configuracion_producto=configuracion_portal)

    return render(
        request,
        'contractors/solicitud_contratista.html',
        {
            'branding': branding,
            'configuracion_portal': configuracion_portal,
            'configuracion_producto': configuracion_portal,
            'formulario': formulario,
            'organizacion': None,
        },
    )


@login_required(login_url='/login/')
def documentos_solicitud_contratista_view(request, solicitud_id):
    configuracion_portal = _obtener_configuracion_portal_activa(request)
    solicitud = obtener_solicitud_contratista(
        solicitud_id,
        configuracion_portal=configuracion_portal,
        usuario=request.user,
    )
    if not solicitud:
        raise Http404('solicitud_contratista_no_encontrada')

    branding = obtener_contexto_branding_con_defaults(configuracion_portal)
    documento_registrado = None

    if request.method == 'POST':
        formulario = FormularioDocumentoSolicitudContratista(request.POST, request.FILES, solicitud=solicitud)
        if formulario.is_valid():
            archivo = formulario.cleaned_data['archivo']
            datos = DatosDocumentoSolicitudContratista(
                tipo_documento=formulario.cleaned_data['tipo_documento'],
                archivo=archivo,
                nombre_original=archivo.name,
                content_type=getattr(archivo, 'content_type', ''),
                tamano_archivo=getattr(archivo, 'size', 0),
            )
            try:
                documento_registrado = registrar_documento_solicitud_contratista(
                    solicitud=solicitud,
                    datos=datos,
                )
                formulario = FormularioDocumentoSolicitudContratista(solicitud=solicitud)
            except ValidationError as exc:
                formulario.add_error(None, _mensajes_validacion(exc))
    else:
        formulario = FormularioDocumentoSolicitudContratista(solicitud=solicitud)

    documentos = listar_documentos_solicitud_contratista(solicitud)
    return render(
        request,
        'contractors/documentos_solicitud_contratista.html',
        {
            'branding': branding,
            'documento_registrado': documento_registrado,
            'documentos': documentos,
            'formulario': formulario,
            'configuracion_portal': configuracion_portal,
            'organizacion': None,
            'solicitud': solicitud,
        },
    )


def buscar_empresas_contratistas_view(request):
    _obtener_configuracion_portal_activa(request)
    query = (request.GET.get('q') or '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})

    resultados = _buscar_empresas_con_prioridad(query)
    return JsonResponse({
        'results': [
            {
                'id': empresa.id,
                'nombre': empresa.nombre,
                'razon_social': empresa.razon_social,
                'nit': empresa.nit,
                'tipo_coincidencia': getattr(empresa, 'tipo_coincidencia_busqueda', 'sugerencia'),
            }
            for empresa in resultados[:8]
        ]
    })


@login_required(login_url='/login/')
@require_POST
def analizar_contrato_contratista_view(request):
    _obtener_configuracion_portal_activa(request)
    contrato = request.FILES.get('contrato_actual') or request.FILES.get('contrato')
    if not contrato:
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': True,
                'error': 'Carga el contrato vigente en PDF.',
            },
            status=400,
        )

    error_pdf = _validar_pdf_temporal_contrato(contrato)
    if error_pdf:
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': True,
                'error': error_pdf,
            },
            status=400,
        )

    analysis_input_hash = calcular_hash_analisis_contractual_desde_datos(
        request.POST,
        archivo_hash=_hash_temporal_archivo(contrato),
    )
    analysis_generated_at = timezone.now().isoformat()

    if not _tratamiento_datos_aceptado(request):
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': False,
                'error': 'Debes aceptar la autorizacion de tratamiento de datos antes de analizar el contrato.',
            },
            status=400,
        )

    if not _permitir_analisis_contrato(request):
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': True,
                'error': 'Has realizado varios analisis en poco tiempo. Intenta nuevamente en unos segundos.',
            },
            status=429,
        )

    registrar_evento_timeline_prestador(
        tipo_evento='ANALISIS_IA_CONTRATO',
        titulo='Inicio de analisis IA de contrato',
        descripcion='Se inicio analisis automatico de contrato desde el flujo publico.',
        estado_resultante='INICIADO',
        metadata={'content_type': getattr(contrato, 'content_type', ''), 'size': getattr(contrato, 'size', 0)},
        usuario=request.user,
        request=request,
    )

    resultado = analizar_contrato_con_openai(contrato)
    if resultado.habilitado and resultado.exito and resultado.es_contrato is False:
        registrar_evento_timeline_prestador(
            tipo_evento='ANALISIS_IA_CONTRATO',
            titulo='Analisis IA de contrato bloqueado',
            descripcion='El documento analizado no fue clasificado como contrato valido.',
            estado_resultante='FALLIDO',
            metadata=resultado.metadata_segura(),
            usuario=request.user,
            request=request,
        )
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': False,
                'es_contrato': False,
                'error': 'El documento cargado no parece ser un contrato valido.',
            },
        )

    if not resultado.habilitado or not resultado.exito:
        registrar_evento_timeline_prestador(
            tipo_evento='ANALISIS_IA_CONTRATO',
            titulo='Analisis IA de contrato fallido',
            descripcion='No fue posible completar el analisis automatico del contrato.',
            estado_resultante='FALLIDO',
            metadata=resultado.metadata_segura(),
            usuario=request.user,
            request=request,
        )
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': True,
                'es_contrato': resultado.es_contrato,
                'error': _mensaje_error_analisis_contrato(resultado),
                'metadata': resultado.metadata_segura(),
            },
        )

    inconsistencia_identidad = _resolver_inconsistencia_identidad_contrato(
        documento_ingresado=request.POST.get('numero_documento') or request.POST.get('document_number'),
        documento_detectado=resultado.documento_contratista,
    )
    if inconsistencia_identidad:
        metadata = resultado.metadata_segura()
        metadata.update({
            **inconsistencia_identidad,
            'analysis_input_hash': analysis_input_hash,
            'analysis_generated_at': analysis_generated_at,
        })
        registrar_evento_timeline_prestador(
            tipo_evento='ANALISIS_IA_CONTRATO',
            titulo='Inconsistencia de identidad detectada',
            descripcion='El documento detectado en contrato no coincide con el documento ingresado.',
            estado_resultante='INCONSISTENCIA_IDENTIDAD',
            metadata=metadata,
            usuario=request.user,
            request=request,
        )
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': False,
                'es_contrato': resultado.es_contrato,
                'error': 'El documento detectado en el contrato no coincide con el numero de documento ingresado.',
                'bloqueos': ['inconsistencia_identidad_documento_contrato'],
                'metadata': metadata,
            },
        )

    registrar_evento_timeline_prestador(
        tipo_evento='ANALISIS_IA_CONTRATO',
        titulo='Analisis IA de contrato completado',
        descripcion='Se completo analisis automatico de contrato con metadata segura.',
        estado_resultante='COMPLETADO',
        metadata=resultado.metadata_segura(),
        usuario=request.user,
        request=request,
    )
    analisis_seguro = enriquecer_analisis_contrato_prestador(resultado)
    analisis_seguro.metadata.update({
        'analysis_input_hash': analysis_input_hash,
        'analysis_generated_at': analysis_generated_at,
    })
    for evento_seguro in analisis_seguro.eventos:
        registrar_evento_timeline_prestador(
            tipo_evento='ANALISIS_IA_CONTRATO',
            titulo=_titulo_evento_analisis_contractual(evento_seguro),
            descripcion='Se registro evento derivado del analisis contractual seguro.',
            estado_resultante=evento_seguro,
            metadata={
                'evento_seguro': evento_seguro,
                'metadata_segura': analisis_seguro.metadata,
                'sugerencia_empresa': analisis_seguro.sugerencia_empresa.como_dict(),
            },
            usuario=request.user,
            request=request,
        )

    return JsonResponse(
        {
            'success': True,
            'manual_allowed': True,
            'es_contrato': resultado.es_contrato,
            'datos': analisis_seguro.datos,
            'campos_no_encontrados': list(resultado.campos_no_encontrados),
            'advertencias': list(analisis_seguro.advertencias),
            'bloqueos': list(analisis_seguro.bloqueos),
            'confianza_general': float(resultado.confianza_general),
            'requiere_confirmacion_usuario': True,
            'requiere_revision_manual': analisis_seguro.requiere_revision_manual,
            'metadata': {
                **resultado.metadata_segura(),
                'analysis_input_hash': analysis_input_hash,
                'analysis_generated_at': analysis_generated_at,
                'analisis_contractual_seguro': analisis_seguro.metadata,
            },
            'analisis_contractual_seguro': analisis_seguro.como_dict(),
            'sugerencia_empresa': analisis_seguro.sugerencia_empresa.como_dict(),
        },
    )


def terminos_condiciones_contratistas_view(request):
    configuracion_portal = _obtener_configuracion_portal_activa(request)
    return render(
        request,
        'contractors/legal_contratistas.html',
        {
            'branding': obtener_contexto_branding_con_defaults(configuracion_portal),
            'tipo_legal': 'terminos',
            'titulo': 'Términos y Condiciones',
            'fecha_actualizacion': 'Mayo de 2026',
            'contenido': (
                'Estos términos regulan el uso del portal digital de Prestadores de Servicios de Aprobado. '
                'Aplican junto con las condiciones generales de los canales digitales de Aprobado y con las '
                'autorizaciones que el usuario acepte durante el registro, cargue documental y validación de su solicitud.'
            ),
            'nota_contratistas': (
                'El portal permite iniciar una solicitud como Prestador de Servicios con contrato vigente; la radicación, '
                'carga de documentos o simulación no constituye aprobación automática ni obligación de desembolso.'
            ),
            'secciones': [
                (
                    'Objeto del portal',
                    'Aprobado habilita el portal de Prestadores de Servicios para que personas con contrato vigente registren su información, seleccionen una empresa existente del ecosistema Aprobado, carguen documentos y avancen en una evaluación preliminar de crédito de libranza o adelanto asociado a su contrato.',
                ),
                (
                    'Condiciones de uso',
                    'El usuario debe utilizar el portal únicamente para gestionar su propia solicitud, mantener la confidencialidad de sus credenciales y abstenerse de cargar documentos alterados, incompletos, ilegibles o que no correspondan a su identidad y relación contractual.',
                ),
                (
                    'Registro de usuario',
                    'Para acceder al flujo de solicitud, el usuario debe autenticarse o crear una cuenta. Aprobado podrá usar los datos registrados para identificar al solicitante, dar continuidad al proceso y conservar trazabilidad operativa del estado de la solicitud.',
                ),
                (
                    'Veracidad de la información',
                    'La información personal, contractual, laboral, financiera y documental suministrada debe ser veraz, completa y actualizada. La entrega de información falsa o inexacta puede generar rechazo de la solicitud, bloqueo del proceso o acciones permitidas por la ley.',
                ),
                (
                    'Carga documental',
                    'El solicitante debe cargar los documentos requeridos, incluyendo documento de identidad, contrato vigente y certificado bancario cuando aplique. Estos documentos se usan para revisión de identidad, relación contractual, empresa contratante, valores, vigencia y consistencia de la solicitud.',
                ),
                (
                    'Validaciones internas',
                    'Aprobado podrá realizar revisión documental, validación de capacidad contractual, evaluación de riesgo, verificación de crédito previo, reglas de segundo crédito o recogida de cartera, y otras validaciones internas o externas que se habiliten en fases posteriores.',
                ),
                (
                    'Simulación y no aprobación automática',
                    'Los valores simulados son informativos. La simulación no garantiza aprobación, cupo, tasa, plazo, comisión, desembolso ni emisión de pagaré. Las condiciones definitivas dependen de la revisión documental, políticas vigentes, capacidad, riesgo y aprobaciones internas.',
                ),
                (
                    'Empresa y pagador existente',
                    'El solicitante debe seleccionar una empresa existente en Aprobado. El portal público no crea empresas ni pagadores. Cuando el flujo avance a etapas productivas, el pagador podrá recibir la novedad operativa que corresponda según las reglas del producto.',
                ),
                (
                    'Responsabilidad del usuario',
                    'El usuario es responsable de revisar la información registrada, confirmar los datos extraídos o solicitados, corregir inconsistencias oportunamente y leer cuidadosamente cualquier autorización, contrato, pagaré o documento legal antes de aceptarlo o firmarlo.',
                ),
                (
                    'Protección de datos',
                    'El tratamiento de datos personales se rige por la política de privacidad de Aprobado, la autorización otorgada por el titular y las normas colombianas aplicables. El usuario puede ejercer sus derechos a través de los canales de atención publicados.',
                ),
                (
                    'Modificaciones',
                    'Aprobado podrá actualizar estos términos para reflejar cambios legales, operativos, tecnológicos o de producto. La versión vigente estará disponible en el portal y tendrá efecto desde su publicación, salvo disposición legal diferente.',
                ),
            ],
        },
    )


def politica_privacidad_contratistas_view(request):
    configuracion_portal = _obtener_configuracion_portal_activa(request)
    return render(
        request,
        'contractors/legal_contratistas.html',
        {
            'branding': obtener_contexto_branding_con_defaults(configuracion_portal),
            'tipo_legal': 'privacidad',
            'titulo': 'Política de Privacidad',
            'fecha_actualizacion': 'Mayo de 2026',
            'contenido': (
                'Esta política describe el tratamiento de datos personales realizado por Aprobado en el portal '
                'de Prestadores de Servicios, de conformidad con la Ley 1581 de 2012, el Decreto 1377 de 2013 y las normas '
                'colombianas aplicables en materia de protección de datos personales.'
            ),
            'nota_contratistas': (
                'La información suministrada en el flujo de Prestadores de Servicios se usa para registrar la solicitud, '
                'validar identidad, revisar documentos, evaluar capacidad contractual y atender el proceso de crédito.'
            ),
            'secciones': [
                (
                    'Responsable del tratamiento',
                    'APROBADO SOLUCIONES DIGITALES SAS actúa como responsable del tratamiento de los datos personales recolectados a través del portal de Prestadores de Servicios, formularios, documentos cargados y canales digitales asociados. Los canales de contacto son Info@aprobado.com.co y +57 315 856 2162.',
                ),
                (
                    'Datos recolectados',
                    'Podemos recolectar datos de identificación, contacto, dirección, correo electrónico, celular, información contractual, empresa seleccionada, tipo de contrato, fechas, valores, documentos de identidad, contrato vigente, certificado bancario, IP, navegador y trazabilidad del uso del portal.',
                ),
                (
                    'Finalidad del tratamiento',
                    'Los datos se tratan para registrar solicitudes, validar identidad, revisar documentos, estimar condiciones, evaluar capacidad contractual, aplicar políticas de riesgo, prevenir fraude, atender consultas, conservar trazabilidad, cumplir obligaciones legales y mejorar la operación del portal.',
                ),
                (
                    'Tratamiento de documentos',
                    'Los documentos cargados, incluyendo cédula, contrato vigente y certificado bancario, se usan para el análisis de la solicitud, validaciones internas, verificación de identidad, revisión contractual y cumplimiento de obligaciones legales u operativas. No se publican ni se entregan a terceros no autorizados.',
                ),
                (
                    'Autorización y consentimiento',
                    'Al aceptar esta política y continuar con el registro, el titular autoriza el tratamiento de sus datos para las finalidades descritas. Cuando una finalidad requiera autorización adicional o expresa, Aprobado podrá solicitarla mediante los mecanismos digitales disponibles.',
                ),
                (
                    'Derechos del titular',
                    'El titular puede conocer, actualizar, rectificar y solicitar supresión de sus datos cuando sea procedente; solicitar prueba de la autorización; ser informado sobre el uso de sus datos; presentar quejas ante la Superintendencia de Industria y Comercio; y revocar la autorización en los casos permitidos por la ley.',
                ),
                (
                    'Canales de atención',
                    'Para consultas, reclamos, actualización de datos, solicitudes de supresión o ejercicio de derechos, el titular puede escribir a Info@aprobado.com.co o comunicarse al +57 315 856 2162. Aprobado atenderá las solicitudes conforme a los términos legales aplicables.',
                ),
                (
                    'Seguridad de la información',
                    'Aprobado aplica medidas administrativas, técnicas y organizacionales razonables para proteger la información contra acceso no autorizado, pérdida, alteración, uso indebido o divulgación no autorizada. El acceso interno se limita según roles y necesidades operativas.',
                ),
                (
                    'Encargados y terceros',
                    'Aprobado podrá apoyarse en proveedores tecnológicos, almacenamiento, validación documental, firma electrónica, mensajería, analítica, entidades financieras o autoridades cuando sea requerido, siempre bajo medidas razonables de confidencialidad y seguridad.',
                ),
                (
                    'Vigencia y conservación',
                    'Los datos se conservarán durante el tiempo necesario para cumplir las finalidades autorizadas, atender obligaciones legales, contables, contractuales, tributarias, de auditoría o defensa jurídica, y luego serán tratados conforme a las políticas de conservación y eliminación aplicables.',
                ),
                (
                    'Contacto',
                    'El canal principal de atención para privacidad y solicitudes relacionadas con datos personales es Info@aprobado.com.co. También puedes comunicarte por WhatsApp o teléfono al +57 315 856 2162.',
                ),
            ],
        },
    )


def _obtener_configuracion_portal_activa(request):
    configuracion_portal = getattr(request, 'configuracion_portal_contratistas', None)
    if not configuracion_portal or not configuracion_portal.activo:
        if getattr(settings, 'DEBUG', False):
            host = request.get_host()
            hosts_activos = listar_hosts_configuracion_portal_contratistas_activos()
            hosts = ', '.join(hosts_activos) if hosts_activos else 'sin hosts activos'
            raise Http404(
                'configuracion_portal_contratistas_no_encontrada. '
                f'Host recibido: {host}. Hosts configurados activos: {hosts}. '
                'Ejecute: python manage.py seed_prestadores_qa_local --host contratistas.localhost:8000',
            )
        raise Http404('configuracion_portal_contratistas_no_encontrada')
    return configuracion_portal


def _obtener_organizacion_activa(request):
    organizacion = getattr(request, 'contractor_organization', None)
    if not organizacion or not organizacion.is_active:
        raise Http404('organizacion_contratista_no_encontrada')
    return organizacion


def _obtener_ip_cliente(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _buscar_empresas_con_prioridad(query):
    base = (
        Empresa.objects
        .filter(convenio_activo=True)
        .exclude(tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA)
    )
    query_normalizada = _normalizar_texto_empresa(query)
    nit_query = _normalizar_nit(query)
    resultados = []
    ids_usados = set()

    if nit_query:
        for empresa in base:
            if _normalizar_nit(empresa.nit) == nit_query:
                empresa.tipo_coincidencia_busqueda = 'nit_exacto'
                resultados.append(empresa)
                ids_usados.add(empresa.id)

    if query_normalizada:
        for empresa in base.exclude(pk__in=ids_usados).order_by('nombre'):
            nombres = {
                _normalizar_texto_empresa(empresa.nombre),
                _normalizar_texto_empresa(empresa.razon_social),
            }
            if query_normalizada in nombres:
                empresa.tipo_coincidencia_busqueda = 'nombre_exacto'
                resultados.append(empresa)
                ids_usados.add(empresa.id)

    sugerencias = (
        base
        .exclude(pk__in=ids_usados)
        .filter(nombre__icontains=query)
        .order_by('nombre')[:8]
    )
    for empresa in sugerencias:
        empresa.tipo_coincidencia_busqueda = 'sugerencia'
        resultados.append(empresa)
        ids_usados.add(empresa.id)

    if len(resultados) < 8:
        sugerencias_razon = (
            base
            .exclude(pk__in=ids_usados)
            .filter(razon_social__icontains=query)
            .order_by('nombre')[:8 - len(resultados)]
        )
        for empresa in sugerencias_razon:
            empresa.tipo_coincidencia_busqueda = 'sugerencia'
            resultados.append(empresa)

    return resultados


def _normalizar_nit(valor):
    return ''.join(caracter for caracter in str(valor or '') if caracter.isdigit())


def _normalizar_texto_empresa(valor):
    texto = str(valor or '').strip().lower()
    texto = ''.join(
        caracter
        for caracter in unicodedata.normalize('NFKD', texto)
        if not unicodedata.combining(caracter)
    )
    texto = re.sub(r'\b(s\.?a\.?s\.?|s\.?a\.?|ltda\.?|limitada)\b', ' ', texto)
    texto = re.sub(r'[^a-z0-9 ]+', ' ', texto)
    return re.sub(r'\s+', ' ', texto).strip()


def _resolver_inconsistencia_identidad_contrato(*, documento_ingresado, documento_detectado):
    ingresado = _normalizar_nit(documento_ingresado)
    detectado = _normalizar_nit(documento_detectado)
    if detectado and ingresado and detectado != ingresado:
        return {
            'tipo_inconsistencia': 'identidad_documento_contrato',
            'documento_ingresado_enmascarado': _enmascarar_documento(ingresado),
            'documento_detectado_enmascarado': _enmascarar_documento(detectado),
        }
    return {}


def _registrar_huella_analisis_contractual_obsoleto(request, formulario):
    metadata = getattr(formulario, 'cleaned_data', {}).get('analisis_contractual_metadata') or {}
    if not metadata.get('analysis_obsolete'):
        return None
    return registrar_evento_timeline_prestador(
        tipo_evento='ANALISIS_IA_CONTRATO',
        titulo='COMPORTAMIENTO_DIGITAL_ANALISIS_CONTRATO_OBSOLETO',
        descripcion='El usuario modifico datos relevantes despues de analizar el contrato; se exige reanalisis.',
        estado_resultante='CONTRATO_ANALISIS_OBSOLETO',
        metadata={
            'reason': 'analysis_input_hash_mismatch',
            'fields_changed': list(CAMPOS_HASH_ANALISIS_CONTRACTUAL) + ['contrato_actual'],
            'requires_reanalysis': True,
            'previous_analysis_generated_at': metadata.get('analysis_generated_at') or '',
            'has_previous_blocking_reasons': bool(metadata.get('bloqueos')),
        },
        usuario=request.user,
        request=request,
    )


def _registrar_huella_empresa_contrato_no_coincide(request, formulario):
    metadata = getattr(formulario, 'cleaned_data', {}).get('analisis_contractual_metadata') or {}
    bloqueos = set(metadata.get('bloqueos') or ())
    if 'EMPRESA_CONTRATO_NO_COINCIDE' not in bloqueos:
        return None

    sugerencia_empresa = metadata.get('sugerencia_empresa') or {}
    return registrar_evento_timeline_prestador(
        tipo_evento='ANALISIS_IA_CONTRATO',
        titulo='COMPORTAMIENTO_DIGITAL_EMPRESA_NO_COINCIDE',
        descripcion='La empresa seleccionada no coincide con la empresa detectada en el contrato.',
        estado_resultante='EMPRESA_CONTRATO_NO_COINCIDE',
        metadata={
            'tipo_coincidencia_empresa': sugerencia_empresa.get('tipo_coincidencia') or '',
            'empresa_detectada_id': sugerencia_empresa.get('empresa_id') or '',
            'requiere_revision_manual': True,
            'raw_empresa_no_incluida': True,
        },
        usuario=request.user,
        request=request,
    )


def _enmascarar_documento(valor):
    valor = _normalizar_nit(valor)
    if len(valor) <= 4:
        return '*' * len(valor)
    return f"{'*' * (len(valor) - 4)}{valor[-4:]}"


def _validar_pdf_temporal_contrato(archivo):
    content_type = getattr(archivo, 'content_type', '')
    extension = Path(getattr(archivo, 'name', '') or '').suffix.lower()
    tamano = getattr(archivo, 'size', 0) or 0

    if content_type != 'application/pdf' or extension != '.pdf':
        return 'El contrato vigente debe cargarse en PDF.'
    if tamano <= 0:
        return 'El contrato vigente esta vacio.'
    if tamano > TAMANO_MAXIMO_DOCUMENTO_BYTES:
        return 'El contrato vigente supera el tamano maximo permitido.'
    return ''


def _hash_temporal_archivo(archivo):
    posicion = None
    if hasattr(archivo, 'tell') and hasattr(archivo, 'seek'):
        try:
            posicion = archivo.tell()
            archivo.seek(0)
        except Exception:
            posicion = None
    digest = hashlib.sha256()
    for bloque in getattr(archivo, 'chunks', lambda: iter(lambda: archivo.read(8192), b''))():
        if not bloque:
            break
        digest.update(bloque)
    if posicion is not None:
        archivo.seek(posicion)
    return digest.hexdigest()


def _tratamiento_datos_aceptado(request):
    valor = (
        request.POST.get('tratamiento_datos_analisis_ia')
        or request.POST.get('tratamiento_datos_aceptado')
        or ''
    )
    return str(valor).strip().lower() in {'1', 'true', 'on', 'si', 'sí'}


def _permitir_analisis_contrato(request, *, limite=3, ventana=20):
    usuario_id = getattr(request.user, 'id', None) or 'anon'
    ip = _obtener_ip_cliente(request) or 'sin-ip'
    llave = f'contractors:analisis-contrato:{usuario_id}:{ip}'
    try:
        intentos = cache.get(llave, 0)
        if intentos >= limite:
            return False
        cache.set(llave, intentos + 1, ventana)
    except Exception:
        return True
    return True


def _mensaje_error_analisis_contrato(resultado):
    if resultado.error == 'cuota_openai_excedida':
        return (
            'El servicio de IA no esta disponible por cuota o facturacion de OpenAI. '
            'Puedes completar la informacion manualmente.'
        )
    if resultado.error == 'openai_api_key_no_configurada':
        return 'El servicio de IA no esta configurado. Puedes completar la informacion manualmente.'
    if resultado.error == 'ia_deshabilitada':
        return 'El analisis automatico esta deshabilitado. Puedes completar la informacion manualmente.'
    return 'No fue posible analizar automaticamente el contrato. Puedes completar la informacion manualmente.'


def _titulo_evento_analisis_contractual(evento):
    titulos = {
        'CONTRATO_FECHA_FINAL_INFERIDA': 'Fecha final de contrato inferida',
        'CONTRATO_VALOR_PENDIENTE_INFERIDO': 'Valor pendiente de contrato inferido',
        'CONTRATO_VALOR_PENDIENTE_NO_DETERMINADO': 'Valor pendiente de contrato no determinado',
        'CONTRATO_VENCIDO_DETECTADO': 'Contrato vencido detectado',
        'EMPRESA_SUGERIDA_POR_NIT': 'Empresa sugerida por NIT',
        'EMPRESA_REQUIERE_CONFIRMACION': 'Empresa requiere confirmacion',
    }
    return titulos.get(evento, 'Evento de analisis contractual')


def _payload_simulacion(resultado):
    return {llave: str(valor) for llave, valor in resultado.como_dict().items()}


def _resultado_simulacion_json(resultado):
    return {
        'monto_solicitado': str(resultado.monto_solicitado),
        'plazo_meses': resultado.plazo_meses,
        'tasa_mensual': str(resultado.tasa_mensual),
        'tea_calculada': str(resultado.tea_calculada),
        'costo_originacion': str(resultado.costo_originacion),
        'iva_costo_originacion': str(resultado.iva_costo_originacion),
        'fondo_garantia_total': str(resultado.fondo_garantia_total),
        'fondo_garantia_base': str(resultado.fondo_garantia_base),
        'fondo_garantia_iva': str(resultado.fondo_garantia_iva),
        'seguro_vida': str(resultado.seguro_vida),
        'capital_total_financiado': str(resultado.capital_total_financiado),
        'cuota_mensual': str(resultado.cuota_mensual),
        'intereses_estimados': str(resultado.intereses_estimados),
        'total_estimado': str(resultado.total_estimado),
        'version_politica': resultado.version_politica,
        'advertencias': list(resultado.advertencias),
    }


def _registrar_documentos_iniciales(*, solicitud, formulario):
    mapa_documentos = {
        'documento_identidad_frontal': ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_FRONTAL,
        'documento_identidad_reverso': ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_REVERSO,
        'contrato_actual': ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
        'certificado_bancario': ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
    }
    documentos_registrados = {}
    for nombre_campo, tipo_documento in mapa_documentos.items():
        archivo = formulario.cleaned_data[nombre_campo]
        documentos_registrados[tipo_documento] = registrar_documento_solicitud_contratista(
            solicitud=solicitud,
            datos=DatosDocumentoSolicitudContratista(
                tipo_documento=tipo_documento,
                archivo=archivo,
                nombre_original=archivo.name,
                content_type=getattr(archivo, 'content_type', ''),
                tamano_archivo=getattr(archivo, 'size', 0),
            ),
        )
    return documentos_registrados


def _mensajes_validacion(exc):
    if hasattr(exc, 'message_dict'):
        mensajes = []
        for errores in exc.message_dict.values():
            mensajes.extend(str(error) for error in errores)
        return ' '.join(mensajes)
    return str(exc)
