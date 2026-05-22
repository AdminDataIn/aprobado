from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
import logging
import mimetypes
import os
import zipfile
import io
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
import decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Case, CharField, Count, DecimalField, DurationField, ExpressionWrapper, F, Max, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, Concat, Trim
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils._os import safe_join
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods, require_POST
from urllib.parse import quote

from usuarios.models import PerfilPagador, ProductAccessProfile
from usuarios.product_flow import ProductFlowConflict, assign_user_flow, get_flow_home_path, get_flow_label, get_user_flow

from .. import credit_services
from ..decorators import marketplace_admin_required, pagador_required
from ..forms import (
    AbonoManualAdminForm,
    ConsignacionOfflineForm,
    CreditoAdelantoNominaForm,
    CreditoEmprendimientoForm,
    CreditoLibranzaForm,
    EmployeeBulkUploadForm,
    MarketplaceItemForm,
    PagoCreditoOfflineForm,
    PagoMasivoEmpresaConfirmForm,
    PagoMasivoEmpresaUploadForm,
    PagoObligacionesSeleccionadasForm,
    SpecialCaseLibranzaOriginationForm,
    SpecialCaseLibranzaSimulationForm,
)
from ..models import (
    AsesorComercial,
    Credito,
    CreditoAdelantoNomina,
    CreditoReglaEspecialAudit,
    CuentaAhorro,
    Empresa,
    HistorialEstado,
    HistorialPago,
    LotePagoEmpresa,
    MarketplaceItem,
    MarketplaceItemHistorialEstado,
    MovimientoAhorro,
    Notificacion,
    Pagare,
    WompiIntent,
    ZapSignWebhookLog,
)
from ..services.adelanto_nomina_service import evaluar_elegibilidad_adelanto, obtener_vinculo_laboral_activo
from ..services.capacidad_descuento_service import calcular_capacidad_descuento, simular_adelanto_nomina
from ..services.certificado_bancario_service import procesar_certificado_bancario
from ..services.empleados_service import plantilla_empleados_xlsx, procesar_carga_empleados, reconciliar_usuarios_empleados_legacy
from ..services.marketplace_service import cambiar_estado_publicacion, registrar_historial_publicacion
from ..services.tasa_service import obtener_tasa_credito

logger = logging.getLogger(__name__)

MARKETPLACE_COMPANY_LOGOS = {
    'datain': 'images/Convenios/LogoDatain.png',
}


def _rate_limit_simple(request, scope, limit=8, window=60):
    ip = (request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or 'anon').split(',')[0].strip()
    cache_key = f"rate-limit:{scope}:{ip}"
    try:
        hits = cache.get(cache_key, 0)
        if hits >= limit:
            return False
        cache.set(cache_key, hits + 1, window)
    except Exception:
        logger.warning("Rate limit cache unavailable for scope %s", scope, exc_info=True)
    return True


def _obtener_decision_pagador(credito):
    return HistorialEstado.objects.filter(
        credito=credito,
        usuario_modificacion__perfil_pagador__isnull=False,
        estado_nuevo__in=[Credito.EstadoCredito.APROBADO_PAGADOR, Credito.EstadoCredito.RECHAZADO]
    ).order_by('-fecha').first()


def _build_capacidad_descuento_context(credito):
    if credito.linea == Credito.LineaCredito.ADELANTO_NOMINA and getattr(credito, 'detalle_adelanto_nomina', None):
        vinculo = credito.detalle_adelanto_nomina.vinculo_laboral
        return calcular_capacidad_descuento(
            salario=vinculo.salario_base_mensual or Decimal('0.00'),
            auxilio_transporte=vinculo.auxilio_transporte_mensual,
            descuentos=vinculo.descuentos_fijos_mensuales,
            monto_solicitado=credito.monto_solicitado or Decimal('0.00'),
        )
    return None


def _map_wompi_status_to_intent(status):
    if not status:
        return WompiIntent.Estado.PENDING
    normalized = str(status).upper()
    if normalized in {
        WompiIntent.Estado.CREATED,
        WompiIntent.Estado.PENDING,
        WompiIntent.Estado.APPROVED,
        WompiIntent.Estado.DECLINED,
        WompiIntent.Estado.ERROR,
        WompiIntent.Estado.EXPIRED,
    }:
        return normalized
    if normalized in {'VOIDED', 'EXPIRED'}:
        return WompiIntent.Estado.EXPIRED
    return WompiIntent.Estado.PENDING
