from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature

from contractors.models import ContractorApplication
from contractors.services.continuidad_solicitud import bloquear_solicitante, solicitud_en_proceso
from gestion_creditos.models import Empresa


class ContinuidadSolicitudConcurrenciaTest(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_borradores_solo_uno_puede_entrar_a_evaluacion(self):
        usuario = get_user_model().objects.create_user(username='continuidad-concurrente')
        empresa = Empresa.objects.create(nombre='Convenio prueba')
        solicitudes = [ContractorApplication.objects.create(
            usuario=usuario, empresa=empresa, estado='DOCUMENTOS_CARGADOS',
            tipo_documento='CC', numero_documento='123456789', nombres='Ana', apellidos='Perez',
            celular='3001234567', correo='ana@example.com', direccion='Calle 1', cargo='Consultora',
        ) for _ in range(2)]
        barrera = Barrier(2)

        def avanzar(pk):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                with transaction.atomic():
                    bloquear_solicitante(usuario)
                    if solicitud_en_proceso(usuario, excluir=pk):
                        return False
                    solicitud = ContractorApplication.objects.select_for_update().get(pk=pk)
                    solicitud.estado = 'EVALUACION_PENDIENTE'
                    solicitud.save(update_fields=['estado'])
                    return True
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(avanzar, [s.pk for s in solicitudes]))
        self.assertCountEqual(resultados, [True, False])
        self.assertEqual(ContractorApplication.objects.filter(estado='EVALUACION_PENDIENTE').count(), 1)
        self.assertEqual(ContractorApplication.objects.count(), 2)
