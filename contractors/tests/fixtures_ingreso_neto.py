from datetime import date, timedelta
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings

from contractors.models import ContractorApplication, ContractorApplicationDocument
from contractors.services.ingreso_neto import registrar_ingreso_neto, obtener_ingreso_neto_vigente
from gestion_creditos.models import Empresa


def preparar_evidencia_neta(test):
    temporal = TemporaryDirectory()
    test.addCleanup(temporal.cleanup)
    storage = override_settings(PRIVATE_DOCUMENTS_ROOT=temporal.name)
    storage.enable()
    test.addCleanup(storage.disable)
    actor = get_user_model().objects.create_superuser('neto-' + uuid4().hex, '', None)
    solicitud = ContractorApplication.objects.create(usuario=actor, empresa=Empresa.objects.create(nombre='Empresa de test'))
    documento = ContractorApplicationDocument.objects.create(
        solicitud=solicitud, uploaded_by=actor, tipo_documento='CONTRATO',
        archivo=ContentFile(b'%PDF-1.4\nEvidencia sintetica verificable\n%%EOF', name='evidencia.pdf'),
    )
    return solicitud, actor, documento


def ingreso_neto_test(solicitud, actor, documento, monto, *, corte=date(2026, 8, 1)):
    with patch('contractors.services.ingreso_neto.timezone.localdate', return_value=corte):
        registrar_ingreso_neto(solicitud=solicitud, actor=actor, monto=monto,
                              fecha_corte=corte, vigente_hasta=corte + timedelta(days=30),
                              documentos=[documento], observacion='Verificacion sintetica de test')
    return obtener_ingreso_neto_vigente(solicitud, corte=corte)
