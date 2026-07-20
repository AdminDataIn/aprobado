import hashlib
import hmac
import json
import logging
from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from contractors.forms import (
    AtenderSubsanacionPrestadorForm,
    DocumentoPrestadorForm,
    SimulacionPrestadorForm,
    SolicitudPrestadorForm,
)
from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
    RequerimientoSubsanacionPrestador,
)
from contractors.services.capacidad_contractual import (
    evaluar_capacidad_contractual_preliminar,
    obtener_configuracion_simulador_prestador,
    obtener_configuracion_publica_simulador_prestador,
    obtener_version_politica_simulador,
    simular_credito_prestador_informativo,
    snapshot_configuracion_financiera,
)
from contractors.services.analisis_contractual_seguro import analizar_contrato_seguro
from contractors.services.solicitud import (
    actualizar_estado_documental,
    guardar_documento_prestador,
    guardar_documentos_formulario,
    solicitud_tiene_documentos_obligatorios,
)
from contractors.services.evaluacion_audit import (
    ESTADOS_CON_EVALUACION,
    invalidar_evaluacion_si_cambiaron_datos,
    marcar_evaluacion_pendiente,
    registrar_solicitud_creada,
)
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.autorizacion_datacredito import (
    registrar_autorizacion_datacredito_desde_solicitud,
)
from contractors.services.presentacion_solicitud import construir_estado_publico_solicitud
from contractors.services.subsanacion import atender_requerimiento_subsanacion


logger = logging.getLogger(__name__)


CLAVE_SESION_ANALISIS_CONTRATO = 'contractors_analisis_contrato_v1'
ESTADOS_ANALISIS_PERMITIDOS = {'COMPLETADO', 'CON_ADVERTENCIAS', 'NO_DISPONIBLE'}


def inicio_prestadores_view(request):
    return redirect('contractors:solicitar')


@login_required
@require_POST
def analizar_contrato_prestador_view(request, solicitud_id=None):
    solicitud = None
    if solicitud_id is not None:
        solicitud = _obtener_solicitud_del_usuario(solicitud_id, request.user)

    autorizacion_enviada = _valor_booleano(request.POST.get('autoriza_analisis_contractual_asistido'))
    autorizacion_guardada = bool(
        solicitud and solicitud.autoriza_analisis_contractual_asistido
    )
    if not (autorizacion_enviada or autorizacion_guardada):
        return JsonResponse(
            {
                'success': False,
                'manual_allowed': True,
                'error': 'Debes autorizar el análisis contractual asistido antes de analizar el contrato.',
            },
            status=400,
        )

    documento = request.FILES.get('contrato_actual')
    if documento is None and solicitud is not None:
        contrato = solicitud.documentos.filter(
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
        ).first()
        documento = contrato.archivo if contrato and contrato.archivo else None
    if documento is None:
        return JsonResponse(
            {'success': False, 'manual_allowed': True, 'error': 'Carga el contrato vigente en PDF.'},
            status=400,
        )
    if not documento.name.lower().endswith('.pdf') or documento.size > 8 * 1024 * 1024:
        return JsonResponse(
            {'success': False, 'manual_allowed': True, 'error': 'El contrato debe ser un PDF de máximo 8MB.'},
            status=400,
        )

    try:
        documento.open('rb')
        encabezado = documento.read(5)
        documento.seek(0)
    except (FileNotFoundError, OSError):
        return JsonResponse(
            {'success': False, 'manual_allowed': True, 'error': 'El archivo del contrato no está disponible.'},
            status=400,
        )
    if not encabezado.startswith(b'%PDF'):
        return JsonResponse(
            {'success': False, 'manual_allowed': True, 'error': 'El archivo cargado no es un PDF válido.'},
            status=400,
        )

    hash_archivo = _hash_archivo(documento)

    numero_documento = (
        request.POST.get('numero_documento')
        or (solicitud.numero_documento if solicitud else '')
    )
    if not numero_documento:
        return JsonResponse(
            {'success': False, 'manual_allowed': True, 'error': 'Ingresa el número de documento antes de analizar.'},
            status=400,
        )

    contexto_solicitud = SimpleNamespace(numero_documento=numero_documento)
    resultado = analizar_contrato_seguro(
        solicitud=contexto_solicitud,
        documento=documento,
    )
    evidencia = {
        'archivo_hash_sha256': hash_archivo,
        'estado': resultado.estado,
        'analizado_en': timezone.now().isoformat(),
        'analizado_en_epoch': int(timezone.now().timestamp()),
        'documento_hash': _hash_documento(numero_documento),
        'metadata_segura': resultado.metadata,
    }
    request.session[CLAVE_SESION_ANALISIS_CONTRATO] = evidencia
    request.session.modified = True
    if solicitud is not None:
        version_anterior = construir_version_datos(solicitud)[0]
        solicitud.estado_analisis_contractual = resultado.estado
        solicitud.metadata_analisis_contractual = {
            **resultado.metadata,
            'archivo_hash_sha256': hash_archivo,
            'documento_hash': evidencia['documento_hash'],
        }
        solicitud.fecha_analisis_contractual = timezone.now()
        solicitud.save(update_fields=[
            'estado_analisis_contractual',
            'metadata_analisis_contractual',
            'fecha_analisis_contractual',
            'updated_at',
        ])
        invalidar_evaluacion_si_cambiaron_datos(
            solicitud,
            version_anterior=version_anterior,
            usuario=request.user,
            campos=['analisis_contractual', 'contrato_hash_sha256'],
            motivo='analisis_contractual_actualizado',
        )
    return JsonResponse(resultado.respuesta_publica())


def _valor_booleano(valor):
    return str(valor or '').strip().lower() in {'1', 'true', 'on', 'yes', 'si', 'sí'}


def _hash_archivo(archivo):
    archivo.open('rb')
    digest = hashlib.sha256()
    if hasattr(archivo, 'chunks'):
        for bloque in archivo.chunks():
            digest.update(bloque)
    else:
        while True:
            bloque = archivo.read(64 * 1024)
            if not bloque:
                break
            digest.update(bloque)
    archivo.seek(0)
    return digest.hexdigest()


def _hash_documento(numero_documento):
    normalizado = ''.join(caracter for caracter in str(numero_documento or '') if caracter.isdigit())
    return hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        normalizado.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _validar_evidencia_analisis(*, request, form, solicitud):
    archivo = form.cleaned_data.get('contrato_actual')
    if archivo is None and solicitud is not None:
        contrato = solicitud.documentos.filter(
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
        ).first()
        archivo = contrato.archivo if contrato and contrato.archivo else None
    if archivo is None:
        return {}, 'Carga y analiza el contrato antes de registrar la solicitud.'

    hash_archivo = _hash_archivo(archivo)
    hash_documento = _hash_documento(form.cleaned_data.get('numero_documento'))
    evidencia_sesion = request.session.get(CLAVE_SESION_ANALISIS_CONTRATO) or {}
    evidencia_modelo = {}
    if solicitud is not None and solicitud.metadata_analisis_contractual:
        evidencia_modelo = {
            'archivo_hash_sha256': solicitud.metadata_analisis_contractual.get('archivo_hash_sha256'),
            'documento_hash': solicitud.metadata_analisis_contractual.get('documento_hash'),
            'estado': solicitud.estado_analisis_contractual,
            'analizado_en': (
                solicitud.fecha_analisis_contractual.isoformat()
                if solicitud.fecha_analisis_contractual else ''
            ),
            'analizado_en_epoch': (
                int(solicitud.fecha_analisis_contractual.timestamp())
                if solicitud.fecha_analisis_contractual else 0
            ),
            'metadata_segura': solicitud.metadata_analisis_contractual,
        }

    evidencia = evidencia_sesion if (
        evidencia_sesion.get('archivo_hash_sha256') == hash_archivo
        and evidencia_sesion.get('documento_hash') == hash_documento
    ) else evidencia_modelo
    if evidencia.get('archivo_hash_sha256') != hash_archivo:
        return {}, 'El contrato cambió después del análisis. Debes analizarlo nuevamente.'
    if evidencia.get('documento_hash') != hash_documento:
        return {}, 'El documento ingresado cambió después del análisis. Debes analizar el contrato nuevamente.'
    if evidencia.get('estado') == ContractorApplication.EstadoAnalisisContractual.BLOQUEADO:
        return {}, 'El análisis contractual tiene un bloqueo que debes resolver antes de continuar.'
    if evidencia.get('estado') not in ESTADOS_ANALISIS_PERMITIDOS:
        return {}, 'Debes analizar el contrato antes de registrar la solicitud.'
    analizado_en = int(evidencia.get('analizado_en_epoch') or 0)
    if not analizado_en or int(timezone.now().timestamp()) - analizado_en > 60 * 60:
        return {}, 'El análisis contractual venció. Debes analizar el contrato nuevamente.'
    return evidencia, ''


def _aplicar_evidencia_analisis(solicitud, evidencia):
    metadata = dict(evidencia.get('metadata_segura') or {})
    metadata['archivo_hash_sha256'] = evidencia['archivo_hash_sha256']
    metadata['documento_hash'] = evidencia['documento_hash']
    solicitud.estado_analisis_contractual = evidencia['estado']
    solicitud.metadata_analisis_contractual = metadata
    solicitud.fecha_analisis_contractual = timezone.now()


def _validar_origenes_cedula(request, form):
    valido = True
    for campo in ('documento_identidad_frontal', 'documento_identidad_reverso'):
        if not form.cleaned_data.get(campo):
            continue
        origen = request.POST.get(f'origen_{campo}', '')
        if origen not in {'camera', 'capture', 'upload_fallback'}:
            form.add_error(campo, 'Captura la cédula desde la cámara del dispositivo.')
            valido = False
        elif origen == 'upload_fallback' and not settings.CONTRACTORS_ALLOW_ID_UPLOAD_FALLBACK:
            form.add_error(campo, 'La carga manual de cédula no está habilitada.')
            valido = False
    return valido


def _metadata_documentos_desde_request(request):
    ahora = timezone.now().isoformat()
    user_agent = request.META.get('HTTP_USER_AGENT', '')[:255]
    metadata = {}
    for campo in ('documento_identidad_frontal', 'documento_identidad_reverso'):
        metadata[campo] = {
            'source': request.POST.get(f'origen_{campo}', ''),
            'captured_at': ahora,
            'user_agent': user_agent,
        }
    for campo in ('certificado_bancario', 'contrato_actual'):
        metadata[campo] = {'source': 'upload', 'captured_at': ahora}
    return metadata


@login_required
def solicitar_prestador_view(request):
    solicitud_id = request.POST.get('solicitud_id') or request.GET.get('solicitud_id')
    solicitud_existente = None
    if solicitud_id:
        solicitud_existente = _obtener_solicitud_del_usuario(solicitud_id, request.user)
    version_anterior = (
        construir_version_datos(solicitud_existente)[0]
        if solicitud_existente is not None else ''
    )

    if request.method == 'POST':
        form = SolicitudPrestadorForm(
            request.POST,
            request.FILES,
            instance=solicitud_existente,
        )
        if form.is_valid():
            origenes_validos = _validar_origenes_cedula(request, form)
            evidencia, error_analisis = _validar_evidencia_analisis(
                request=request,
                form=form,
                solicitud=solicitud_existente,
            )
            if error_analisis:
                form.add_error(None, error_analisis)
            if origenes_validos and not error_analisis:
                with transaction.atomic():
                    solicitud = form.save(commit=False)
                    es_nueva = not solicitud.pk
                    solicitud.usuario = request.user
                    if not solicitud.pk:
                        solicitud.estado = ContractorApplication.Estado.DOCUMENTOS_PENDIENTES
                    _aplicar_evidencia_analisis(solicitud, evidencia)
                    solicitud.save()
                    guardar_documentos_formulario(
                        solicitud=solicitud,
                        cleaned_data=form.cleaned_data,
                        usuario=request.user,
                        metadata_documentos=_metadata_documentos_desde_request(request),
                    )
                    actualizar_estado_documental(solicitud)
                    registrar_autorizacion_datacredito_desde_solicitud(
                        solicitud,
                        usuario=request.user,
                        request=request,
                    )
                    if es_nueva:
                        registrar_solicitud_creada(solicitud, usuario=request.user)
                    else:
                        invalidar_evaluacion_si_cambiaron_datos(
                            solicitud,
                            version_anterior=version_anterior,
                            usuario=request.user,
                            campos=['empresa', 'datos_contractuales', 'autorizaciones'],
                            motivo='formulario_actualizado',
                        )
                request.session.pop(CLAVE_SESION_ANALISIS_CONTRATO, None)
                messages.success(request, 'Información y documentos guardados. Continúa con la simulación.')
                return redirect(f'{reverse("contractors:simular")}?solicitud_id={solicitud.id}')
    else:
        form = SolicitudPrestadorForm(instance=solicitud_existente)

    return render(
        request,
        'contractors/solicitud_prestador.html',
        {
            'form': form,
            'solicitud': solicitud_existente,
            'allow_id_upload_fallback': settings.CONTRACTORS_ALLOW_ID_UPLOAD_FALLBACK,
        },
    )


def legal_prestadores_view(request, seccion):
    contenidos = {
        'terminos': {
            'titulo': 'Términos y condiciones para Prestadores de Servicios',
            'actualizacion': 'Julio de 2026',
            'introduccion': 'Estas condiciones regulan el uso del canal digital de Aprobado para Prestadores de Servicios.',
            'destacado': 'Registrar una solicitud o realizar una simulación no implica aprobación, desembolso ni creación de un crédito.',
            'secciones': (
                ('01', 'Objeto del portal', 'El portal permite registrar datos personales, seleccionar una empresa contratante existente, cargar documentos y preparar una simulación informativa.'),
                ('02', 'Registro y veracidad', 'El usuario debe suministrar información completa, vigente y verificable. Aprobado podrá solicitar aclaraciones o soportes adicionales.'),
                ('03', 'Documentos y análisis contractual', 'Los documentos cargados se usan para gestionar la solicitud. El análisis asistido ayuda a extraer datos del contrato, pero el usuario debe revisarlos y confirmarlos.'),
                ('04', 'Simulación informativa', 'Los valores mostrados son estimaciones sujetas a validación documental, capacidad, políticas internas y verificaciones posteriores.'),
                ('05', 'Uso adecuado', 'El usuario se compromete a no suplantar identidades, alterar documentos ni usar el portal para fines contrarios a la ley.'),
                ('06', 'Modificaciones', 'Aprobado podrá actualizar estas condiciones e informar la versión vigente mediante sus canales institucionales.'),
            ),
        },
        'privacidad': {
            'titulo': 'Política de privacidad para Prestadores de Servicios',
            'actualizacion': 'Julio de 2026',
            'introduccion': 'Describe cómo Aprobado trata la información personal y documental recibida en el portal.',
            'destacado': 'Aprobado aplica controles de acceso y usa la información exclusivamente para gestionar y evaluar la solicitud.',
            'secciones': (
                ('01', 'Responsable del tratamiento', 'Aprobado S.A.S. es responsable del tratamiento de los datos recolectados mediante este canal digital.'),
                ('02', 'Datos recolectados', 'Podemos tratar datos de identificación, contacto, información contractual, empresa contratante y documentos aportados por el titular.'),
                ('03', 'Finalidades', 'La información se utiliza para identificar al solicitante, gestionar su solicitud, validar documentos, analizar el contrato y atender consultas o requerimientos.'),
                ('04', 'Análisis asistido', 'El contrato puede analizarse mediante herramientas asistidas para sugerir campos editables. Este proceso no toma decisiones financieras y requiere confirmación del usuario.'),
                ('05', 'Seguridad y conservación', 'Los documentos no se publican y se protegen mediante controles de acceso. Se conservan durante el tiempo necesario para las finalidades autorizadas y obligaciones aplicables.'),
                ('06', 'Derechos del titular', 'El titular puede conocer, actualizar, rectificar o solicitar la supresión de sus datos, y revocar la autorización cuando legalmente proceda.'),
                ('07', 'Canales de atención', 'Las solicitudes sobre datos personales pueden enviarse a info@aprobado.com.co, indicando la identificación del titular y la petición correspondiente.'),
                ('08', 'Vigencia', 'Esta política rige desde su fecha de actualización y podrá modificarse para reflejar cambios normativos u operativos.'),
            ),
        },
        'centrales': {
            'titulo': 'Autorización para consulta ante centrales de información',
            'actualizacion': 'Julio de 2026',
            'introduccion': 'Explica el alcance de una consulta futura de información financiera o crediticia.',
            'destacado': 'La aceptación registrada en este paso no ejecuta una consulta externa ni representa una aprobación financiera.',
            'secciones': (
                ('01', 'Alcance de la autorización', 'El titular autoriza que, en una etapa posterior y cuando corresponda, Aprobado consulte información relevante para la evaluación de la solicitud.'),
                ('02', 'Momento de la consulta', 'La consulta solo podrá realizarse dentro del proceso de evaluación y bajo una configuración operativa habilitada. No se realiza al cargar documentos ni al ejecutar el análisis contractual.'),
                ('03', 'Finalidad', 'La información podrá utilizarse para verificar identidad, comportamiento financiero, endeudamiento y señales de riesgo conforme a las políticas aplicables.'),
                ('04', 'Ausencia de aprobación automática', 'Una consulta, cuando se ejecute, será solo un insumo de evaluación y no garantiza aprobación, monto, plazo ni desembolso.'),
                ('05', 'Derechos del titular', 'El titular conserva sus derechos de consulta, actualización, rectificación y reclamo ante los operadores de información y ante Aprobado.'),
                ('06', 'Canal de contacto', 'Las inquietudes sobre esta autorización pueden dirigirse a info@aprobado.com.co.'),
            ),
        },
    }
    contenido = contenidos.get(seccion)
    if contenido is None:
        raise Http404('Contenido legal no encontrado.')
    return render(request, 'contractors/legal_prestadores.html', {'contenido': contenido})


@login_required
def documentos_prestador_view(request, solicitud_id):
    solicitud = _obtener_solicitud_del_usuario(solicitud_id, request.user)

    if request.method == 'POST':
        form = DocumentoPrestadorForm(request.POST, request.FILES)
        if form.is_valid():
            guardar_documento_prestador(
                solicitud=solicitud,
                tipo_documento=form.cleaned_data['tipo_documento'],
                archivo=form.cleaned_data['archivo'],
                usuario=request.user,
            )
            actualizar_estado_documental(solicitud)
            messages.success(request, 'Documento cargado correctamente.')
            return redirect('contractors:documentos', solicitud_id=solicitud.id)
    else:
        form = DocumentoPrestadorForm()

    documentos = {
        documento.tipo_documento: documento
        for documento in solicitud.documentos.all()
    }
    etiquetas = dict(ContractorApplicationDocument.TipoDocumento.choices)
    estado_documentos = [
        (tipo, etiquetas.get(tipo, tipo), documentos.get(tipo))
        for tipo in DOCUMENTOS_OBLIGATORIOS_PRESTADOR
    ]
    documentos_pendientes = [
        tipo for tipo in DOCUMENTOS_OBLIGATORIOS_PRESTADOR if tipo not in documentos
    ]

    return render(
        request,
        'contractors/documentos_prestador.html',
        {
            'solicitud': solicitud,
            'form': form,
            'documentos': documentos,
            'estado_documentos': estado_documentos,
            'documentos_pendientes': documentos_pendientes,
            'documentos_obligatorios': DOCUMENTOS_OBLIGATORIOS_PRESTADOR,
            'progreso_documental': calcular_progreso_documental(solicitud),
        },
    )


@login_required
def descargar_documento_prestador_view(request, solicitud_id, documento_id):
    solicitud = _obtener_solicitud_del_usuario(solicitud_id, request.user)
    try:
        documento = solicitud.documentos.get(id=documento_id)
    except ContractorApplicationDocument.DoesNotExist as exc:
        raise Http404('Documento no encontrado.') from exc

    return FileResponse(
        documento.archivo.open('rb'),
        as_attachment=False,
        filename=documento.archivo.name.split('/')[-1],
    )


@login_required
def simular_prestador_view(request):
    solicitud_id = request.POST.get('solicitud_id') or request.GET.get('solicitud_id')
    if not solicitud_id:
        messages.info(request, 'Primero registra tu solicitud para habilitar la simulación.')
        return redirect('contractors:solicitar')

    solicitud = _obtener_solicitud_del_usuario(solicitud_id, request.user)
    documentos_cargados = _solicitud_tiene_documentos_obligatorios(solicitud)
    progreso_documental = calcular_progreso_documental(solicitud)
    configuracion = obtener_configuracion_simulador_prestador()
    if configuracion is None:
        logger.error(
            'Simulador de prestadores sin configuracion financiera activa. solicitud_id=%s',
            solicitud.id,
        )
    capacidad_contractual = evaluar_capacidad_contractual_preliminar(
        solicitud,
        documentos_completos=documentos_cargados,
        configuracion=configuracion,
    )
    analisis_habilita_simulacion = solicitud.estado_analisis_contractual in ESTADOS_ANALISIS_PERMITIDOS
    puede_registrar = bool(
        configuracion and documentos_cargados and analisis_habilita_simulacion
    )
    monto_inicial = solicitud.monto_solicitado or (
        configuracion.monto_minimo if configuracion else None
    )
    plazo_inicial = solicitud.plazo_meses or (
        configuracion.plazo_minimo_meses if configuracion else None
    )
    initial = {'monto': monto_inicial, 'plazo_meses': plazo_inicial}
    form = SimulacionPrestadorForm(
        request.POST or None,
        initial=initial,
        configuracion=configuracion,
    )

    if request.method == 'POST' and form.is_valid():
        if not documentos_cargados:
            form.add_error(None, 'Completa los documentos obligatorios antes de registrar la solicitud.')
        if not analisis_habilita_simulacion:
            form.add_error(None, 'El análisis contractual debe estar vigente antes de registrar la solicitud.')
        if not form.non_field_errors():
            resultado = simular_credito_prestador_informativo(
                monto=form.cleaned_data['monto'],
                plazo_meses=form.cleaned_data['plazo_meses'],
                configuracion=configuracion,
            )
            with transaction.atomic():
                solicitud_bloqueada = ContractorApplication.objects.select_for_update().get(
                    pk=solicitud.pk,
                    usuario=request.user,
                )
                version_anterior, _ = construir_version_datos(solicitud_bloqueada)
                estado_anterior = solicitud_bloqueada.estado
                solicitud_bloqueada.monto_solicitado = resultado.monto_solicitado
                solicitud_bloqueada.plazo_meses = resultado.plazo_meses
                snapshot_financiero = snapshot_configuracion_financiera(configuracion)
                solicitud_bloqueada.version_configuracion_financiera_simulacion = (
                    snapshot_financiero['version']
                )
                solicitud_bloqueada.version_politica_simulacion = (
                    obtener_version_politica_simulador(configuracion)
                )
                solicitud_bloqueada.monto_simulado = resultado.monto_solicitado
                solicitud_bloqueada.plazo_simulado_meses = resultado.plazo_meses
                solicitud_bloqueada.tasa_mensual_simulacion = snapshot_financiero['tasa_mensual']
                solicitud_bloqueada.monto_maximo_configuracion_simulacion = (
                    snapshot_financiero['monto_maximo']
                )
                solicitud_bloqueada.plazo_maximo_configuracion_simulacion = (
                    snapshot_financiero['plazo_maximo_meses']
                )
                solicitud_bloqueada.simulada_en = timezone.now()
                solicitud_bloqueada.save(update_fields=[
                    'monto_solicitado', 'plazo_meses',
                    'version_configuracion_financiera_simulacion',
                    'version_politica_simulacion',
                    'monto_simulado', 'plazo_simulado_meses',
                    'tasa_mensual_simulacion',
                    'monto_maximo_configuracion_simulacion',
                    'plazo_maximo_configuracion_simulacion', 'simulada_en', 'updated_at',
                ])
                if estado_anterior in ESTADOS_CON_EVALUACION:
                    invalidar_evaluacion_si_cambiaron_datos(
                        solicitud_bloqueada,
                        version_anterior=version_anterior,
                        usuario=request.user,
                        campos=['monto_solicitado', 'plazo_meses'],
                        motivo='simulacion_actualizada',
                    )
                else:
                    marcar_evaluacion_pendiente(
                        solicitud_bloqueada,
                        usuario=request.user,
                        motivo='simulacion_registrada',
                    )
            messages.success(
                request,
                'Tu solicitud fue registrada para validación. El siguiente paso es el análisis '
                'de riesgo y validación documental.',
                extra_tags='solicitud-registrada-modal',
            )
            return redirect('contractors:mi_credito')

    return render(
        request,
        'contractors/simulador_prestador.html',
        {
            'solicitud': solicitud,
            'documentos_cargados': documentos_cargados,
            'progreso_documental': progreso_documental,
            'capacidad_contractual': capacidad_contractual,
            'analisis_habilita_simulacion': analisis_habilita_simulacion,
            'puede_registrar': puede_registrar,
            'form': form,
            'configuracion_simulador': configuracion,
            'configuracion_publica_simulador': obtener_configuracion_publica_simulador_prestador(
                configuracion,
            ),
        },
    )


@login_required
@require_POST
def calcular_simulacion_prestador_view(request):
    try:
        payload = json.loads((request.body or b'{}').decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Solicitud de cálculo inválida.'}, status=400)

    solicitud = _obtener_solicitud_del_usuario(payload.get('solicitud_id'), request.user)
    if not _solicitud_tiene_documentos_obligatorios(solicitud):
        return JsonResponse({'ok': False, 'error': 'Completa los documentos obligatorios.'}, status=400)
    if solicitud.estado_analisis_contractual not in ESTADOS_ANALISIS_PERMITIDOS:
        return JsonResponse({'ok': False, 'error': 'El análisis contractual debe estar vigente.'}, status=400)

    configuracion = obtener_configuracion_simulador_prestador()
    if configuracion is None:
        logger.error(
            'Calculo de simulacion bloqueado por configuracion financiera ausente. '
            'solicitud_id=%s',
            solicitud.id,
        )
        return JsonResponse(
            {
                'ok': False,
                'error': 'La simulacion no esta disponible temporalmente por configuracion.',
            },
            status=503,
        )
    form = SimulacionPrestadorForm(
        {
            'monto': payload.get('monto'),
            'plazo_meses': payload.get('plazo_meses'),
        },
        configuracion=configuracion,
    )
    if not form.is_valid():
        return JsonResponse(
            {'ok': False, 'error': 'Selecciona un monto y plazo dentro de los rangos permitidos.'},
            status=400,
        )
    resultado = simular_credito_prestador_informativo(
        monto=form.cleaned_data['monto'],
        plazo_meses=form.cleaned_data['plazo_meses'],
        configuracion=configuracion,
    )
    return JsonResponse({'ok': True, 'resultado': _resultado_simulacion_json(resultado)})


def _resultado_simulacion_json(resultado):
    return {
        clave: str(valor) if hasattr(valor, 'as_tuple') else valor
        for clave, valor in resultado.como_dict().items()
    }


@login_required
def mi_credito_prestador_view(request):
    solicitudes = (
        ContractorApplication.objects
        .filter(usuario=request.user)
        .select_related('empresa')
        .prefetch_related(
            'documentos', 'auditorias_predecision', 'requerimientos_subsanacion',
            'revisiones_manuales',
        )
        .order_by('-created_at', '-id')
    )
    solicitudes_con_progreso = [
        (solicitud, calcular_progreso_documental(solicitud))
        for solicitud in solicitudes
    ]
    solicitud_principal = solicitudes_con_progreso[0] if solicitudes_con_progreso else None
    estados_publicos = {
        solicitud.id: construir_estado_publico_solicitud(solicitud)
        for solicitud, _progreso in solicitudes_con_progreso
    }
    estado_publico_principal = (
        estados_publicos.get(solicitud_principal[0].id) if solicitud_principal else None
    )
    return render(
        request,
        'contractors/mi_credito_prestador.html',
        {
            'solicitudes_con_progreso': solicitudes_con_progreso,
            'solicitud_principal': solicitud_principal,
            'solicitudes_anteriores': solicitudes_con_progreso[1:],
            'estados_publicos': estados_publicos,
            'estado_publico_principal': estado_publico_principal,
        },
    )


@login_required
def atender_subsanacion_prestador_view(request, solicitud_id, requerimiento_id):
    try:
        requerimiento = (
            RequerimientoSubsanacionPrestador.objects
            .select_related('solicitud', 'revision')
            .get(
                id=requerimiento_id,
                solicitud_id=solicitud_id,
                solicitud__usuario=request.user,
            )
        )
    except RequerimientoSubsanacionPrestador.DoesNotExist as exc:
        raise Http404('Requerimiento no encontrado.') from exc

    if request.method == 'POST':
        form = AtenderSubsanacionPrestadorForm(
            request.POST,
            request.FILES,
            requerimiento=requerimiento,
        )
        if form.is_valid():
            try:
                atender_requerimiento_subsanacion(
                    requerimiento,
                    form=form,
                    usuario=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, str(exc))
            except Exception:
                form.add_error(None, 'No fue posible registrar la informacion. Intenta nuevamente.')
            else:
                messages.success(
                    request,
                    'La informacion fue registrada y quedo pendiente de validacion interna.',
                )
                return redirect('contractors:mi_credito')
    else:
        form = AtenderSubsanacionPrestadorForm(requerimiento=requerimiento)
    return render(
        request,
        'contractors/atender_subsanacion_prestador.html',
        {'requerimiento': requerimiento, 'solicitud': requerimiento.solicitud, 'form': form},
    )


def _obtener_solicitud_del_usuario(solicitud_id, usuario):
    try:
        return ContractorApplication.objects.select_related('empresa', 'usuario').get(
            id=solicitud_id,
            usuario=usuario,
        )
    except (ContractorApplication.DoesNotExist, ValueError, TypeError) as exc:
        raise Http404('Solicitud no encontrada.') from exc


def _solicitud_tiene_documentos_obligatorios(solicitud):
    return solicitud_tiene_documentos_obligatorios(solicitud)


def _actualizar_estado_documental(solicitud):
    return actualizar_estado_documental(solicitud)


def calcular_progreso_documental(solicitud):
    documentos = getattr(solicitud, '_prefetched_objects_cache', {}).get('documentos')
    if documentos is None:
        tipos_cargados = set(solicitud.documentos.values_list('tipo_documento', flat=True))
    else:
        tipos_cargados = {documento.tipo_documento for documento in documentos}
    total = len(DOCUMENTOS_OBLIGATORIOS_PRESTADOR)
    cargados = len(set(DOCUMENTOS_OBLIGATORIOS_PRESTADOR).intersection(tipos_cargados))
    porcentaje = int((cargados / total) * 100) if total else 0
    return {
        'cargados': cargados,
        'total': total,
        'porcentaje': porcentaje,
        'completo': cargados == total,
    }
