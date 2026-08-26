from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.test import TestCase
from django.urls import reverse

from gestion_creditos.admin import EmpresaAdmin
from gestion_creditos.models import Empresa
from gestion_creditos.services.empresa_geografia import (
    FUENTE_COORDENADAS_DATASET,
    _cargar_dataset_centroides,
    describir_geografia_empresa,
    normalizar_departamento,
    obtener_catalogo_centroides_municipales,
    obtener_presencia_empresas,
)


class EmpresaGeografiaTests(TestCase):
    def test_empresa_com_coordenadas_es_registrada_y_representable(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Georreferenciada',
            departamento='Meta',
            municipio='Villavicencio',
            latitud=Decimal('4.142000'),
            longitud=Decimal('-73.626600'),
        )

        geografia = describir_geografia_empresa(empresa)

        self.assertTrue(geografia['ubicacion_registrada'])
        self.assertTrue(geografia['ubicacion_representable'])
        self.assertEqual(geografia['fuente_coordenadas'], 'REGISTRADAS')

    def test_municipio_sin_coordenadas_explicitas_usa_dataset(self):
        Empresa.objects.create(
            nombre='Empresa Pereira',
            departamento='Risaralda',
            municipio='Pereira',
        )

        presencia = obtener_presencia_empresas()

        self.assertEqual(presencia['con_ubicacion_registrada'], 1)
        self.assertEqual(presencia['sin_ubicacion_registrada'], 0)
        self.assertEqual(presencia['con_ubicacion_representable'], 1)
        self.assertEqual(len(presencia['mapa_ubicaciones']), 1)
        self.assertEqual(presencia['departamentos_con_presencia'], ['Risaralda'])
        self.assertEqual(presencia['municipios_con_presencia'], ['Pereira'])

    def test_centroide_interno_hace_representable_la_ubicacion(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Centroide',
            departamento=' meta ',
            municipio=' villavicencio ',
        )

        geografia = describir_geografia_empresa(empresa)

        self.assertTrue(geografia['ubicacion_representable'])
        self.assertEqual(geografia['fuente_coordenadas'], FUENTE_COORDENADAS_DATASET)
        self.assertEqual(geografia['latitud'], 4.091675)
        self.assertEqual(geografia['longitud'], -73.492921)

    def test_municipio_sin_centroide_no_genera_marcador(self):
        Empresa.objects.create(
            nombre='Empresa Municipio Sin Centroide',
            departamento='Departamento Inexistente',
            municipio='Municipio Inexistente',
        )

        presencia = obtener_presencia_empresas()

        self.assertEqual(presencia['con_ubicacion_registrada'], 1)
        self.assertEqual(presencia['con_ubicacion_representable'], 0)
        self.assertEqual(presencia['mapa_ubicaciones'], [])

    def test_coordenada_explicita_tiene_prioridad_sobre_dataset(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Coordenadas Propias',
            departamento='Antioquia',
            municipio='Medellin',
            latitud=Decimal('6.200000'),
            longitud=Decimal('-75.500000'),
        )

        geografia = describir_geografia_empresa(empresa)

        self.assertEqual(geografia['fuente_coordenadas'], 'REGISTRADAS')
        self.assertEqual(geografia['latitud'], 6.2)
        self.assertEqual(geografia['longitud'], -75.5)

    def test_lookup_exacto_usa_departamento_y_municipio(self):
        empresa_meta = Empresa.objects.create(
            nombre='Empresa Granada Meta',
            departamento='Meta',
            municipio='Granada',
        )
        empresa_antioquia = Empresa.objects.create(
            nombre='Empresa Granada Antioquia',
            departamento='Antioquia',
            municipio='Granada',
        )

        geografia_meta = describir_geografia_empresa(empresa_meta)
        geografia_antioquia = describir_geografia_empresa(empresa_antioquia)

        self.assertEqual(geografia_meta['fuente_coordenadas'], FUENTE_COORDENADAS_DATASET)
        self.assertEqual(geografia_antioquia['fuente_coordenadas'], FUENTE_COORDENADAS_DATASET)
        self.assertNotEqual(
            (geografia_meta['latitud'], geografia_meta['longitud']),
            (geografia_antioquia['latitud'], geografia_antioquia['longitud']),
        )

    def test_puerto_gaitan_meta_resuelve_sin_tilde_en_entrada(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Puerto Gaitan',
            departamento='META',
            municipio='Puerto Gaitan',
        )

        geografia = describir_geografia_empresa(empresa)

        self.assertTrue(geografia['ubicacion_representable'])
        self.assertEqual(geografia['fuente_coordenadas'], FUENTE_COORDENADAS_DATASET)

    def test_municipio_antioquia_distinto_medellin_es_representable(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Rionegro',
            departamento='Antioquia',
            municipio='Rionegro',
        )

        geografia = describir_geografia_empresa(empresa)

        self.assertTrue(geografia['ubicacion_representable'])
        self.assertEqual(geografia['fuente_coordenadas'], FUENTE_COORDENADAS_DATASET)

    def test_pereira_risaralda_es_representable(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Pereira Dataset',
            departamento='Risaralda',
            municipio='Pereira',
        )

        geografia = describir_geografia_empresa(empresa)

        self.assertTrue(geografia['ubicacion_representable'])
        self.assertEqual(geografia['fuente_coordenadas'], FUENTE_COORDENADAS_DATASET)

    def test_dataset_oficial_se_carga_una_vez_por_proceso(self):
        _cargar_dataset_centroides.cache_clear()

        catalogo_primero = obtener_catalogo_centroides_municipales()
        catalogo_segundo = obtener_catalogo_centroides_municipales()
        cache = _cargar_dataset_centroides.cache_info()

        self.assertIs(catalogo_primero, catalogo_segundo)
        self.assertEqual(len(catalogo_primero['centroides']), 1121)
        self.assertEqual(catalogo_primero['metadata']['version_fuente'], 'MGN 2024')
        self.assertEqual(cache.misses, 1)
        self.assertGreaterEqual(cache.hits, 1)

    def test_empresa_sin_geografia_no_cuenta_como_ubicada(self):
        Empresa.objects.create(nombre='Empresa Sin Geografia')

        presencia = obtener_presencia_empresas()

        self.assertEqual(presencia['con_ubicacion_registrada'], 0)
        self.assertEqual(presencia['sin_ubicacion_registrada'], 1)

    def test_departamento_sin_municipio_es_registrado_pero_incompleto(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Solo Departamento',
            departamento='Meta',
        )

        geografia = describir_geografia_empresa(empresa)

        self.assertTrue(geografia['ubicacion_registrada'])
        self.assertFalse(geografia['ubicacion_completa'])
        self.assertFalse(geografia['ubicacion_representable'])

    def test_placeholders_no_cuentan_como_ubicacion(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Placeholder',
            departamento='Sin departamento registrado',
            municipio='N/A',
        )

        geografia = describir_geografia_empresa(empresa)

        self.assertFalse(geografia['ubicacion_registrada'])
        self.assertFalse(geografia['ubicacion_representable'])

    def test_normalizacion_departamento_limpia_espacios_y_preserva_tildes(self):
        self.assertEqual(normalizar_departamento('  antioquia  '), 'Antioquia')
        self.assertEqual(normalizar_departamento(' bogot\u00e1   d.c. '), 'Bogot\u00e1 D.C.')
        self.assertIsNone(normalizar_departamento('Sin departamento registrado'))

    def test_varias_empresas_en_mismo_municipio_se_agrupan(self):
        for indice in range(2):
            Empresa.objects.create(
                nombre=f'Empresa Meta {indice}',
                departamento='Meta',
                municipio='Villavicencio',
            )

        presencia = obtener_presencia_empresas()

        self.assertEqual(len(presencia['ubicaciones']), 1)
        self.assertEqual(presencia['ubicaciones'][0]['empresas'], 2)
        self.assertEqual(len(presencia['mapa_ubicaciones']), 1)
        self.assertEqual(presencia['mapa_ubicaciones'][0]['empresas'], 2)

    def test_conteos_y_estructura_del_mapa_son_consistentes(self):
        Empresa.objects.create(
            nombre='Empresa Meta',
            departamento='Meta',
            municipio='Villavicencio',
        )
        Empresa.objects.create(
            nombre='Empresa Antioquia',
            departamento='Antioquia',
            municipio='Medellin',
        )
        Empresa.objects.create(nombre='Empresa Sin Ubicacion')

        presencia = obtener_presencia_empresas()

        self.assertEqual(presencia['total_empresas'], 3)
        self.assertEqual(presencia['con_ubicacion_registrada'], 2)
        self.assertEqual(presencia['sin_ubicacion_registrada'], 1)
        self.assertEqual(presencia['con_ubicacion_representable'], 2)
        self.assertEqual(presencia['departamentos_con_presencia'], ['Antioquia', 'Meta'])
        self.assertEqual(len(presencia['municipios_con_presencia']), 2)
        for punto in presencia['mapa_ubicaciones']:
            self.assertEqual(
                set(punto),
                {'departamento', 'municipio', 'latitud', 'longitud', 'empresas'},
            )

    def test_ciudad_legada_no_se_usa_como_fallback(self):
        empresa = Empresa.objects.create(
            nombre='Empresa Solo Ciudad Legada',
            ciudad='Villavicencio',
        )

        geografia = describir_geografia_empresa(empresa)
        presencia = obtener_presencia_empresas()

        self.assertFalse(geografia['ubicacion_registrada'])
        self.assertFalse(geografia['ubicacion_representable'])
        self.assertEqual(presencia['con_ubicacion_registrada'], 0)
        self.assertEqual(presencia['sin_ubicacion_registrada'], 1)
        self.assertEqual(presencia['mapa_ubicaciones'], [])

    def test_resumen_geografico_no_expone_datos_de_empresa_ni_contacto(self):
        Empresa.objects.create(
            nombre='Nombre Interno Sensible',
            nit='900123456',
            correo_contacto='privado@example.com',
            telefono_contacto='3001234567',
            direccion_principal='Direccion privada 123',
            departamento='Meta',
            municipio='Villavicencio',
        )

        resultado = str(obtener_presencia_empresas())

        self.assertNotIn('Nombre Interno Sensible', resultado)
        self.assertNotIn('900123456', resultado)
        self.assertNotIn('privado@example.com', resultado)
        self.assertNotIn('3001234567', resultado)
        self.assertNotIn('Direccion privada 123', resultado)


class EmpresaGeografiaLandingAdminTests(TestCase):
    def test_landing_usa_conteos_geograficos_del_servicio(self):
        Empresa.objects.create(
            nombre='Aliado Meta',
            convenio_activo=True,
            departamento='Meta',
            municipio='Villavicencio',
        )
        Empresa.objects.create(nombre='Aliado Sin Ubicacion', convenio_activo=True)
        Empresa.objects.create(
            nombre='Inactiva Antioquia',
            convenio_activo=False,
            departamento='Antioquia',
            municipio='Medellin',
        )

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        presencia = response.context['landing_presencia']
        self.assertEqual(presencia['con_ubicacion_registrada'], 1)
        self.assertEqual(presencia['sin_ubicacion_registrada'], 1)
        self.assertEqual(presencia['departamentos_con_presencia'], ['Meta'])
        self.assertEqual(presencia['municipios_con_presencia'], ['Villavicencio'])
        self.assertEqual(presencia['mapa_ubicaciones'][0]['empresas'], 1)

    def test_landing_no_genera_halos_ni_depende_de_ciudad(self):
        Empresa.objects.create(
            nombre='Aliado Con Marcador',
            convenio_activo=True,
            departamento='Meta',
            municipio='Villavicencio',
        )

        response = self.client.get(reverse('libranza:landing'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'libranza-map-point-glow')
        self.assertNotContains(response, 'ubicacion.ciudad')
        self.assertContains(response, 'ubicacion.municipio')
        self.assertContains(response, 'Municipios activos')

    def test_admin_empresa_expone_seccion_geografica_opcional(self):
        model_admin = EmpresaAdmin(Empresa, AdminSite())
        fieldsets = dict(model_admin.fieldsets)
        campos = fieldsets['Ubicación / Presencia nacional']['fields']

        self.assertEqual(
            campos,
            (
                'pais', 'departamento', 'municipio',
                'direccion_principal', 'latitud', 'longitud',
            ),
        )
        for nombre_campo in campos:
            self.assertTrue(Empresa._meta.get_field(nombre_campo).blank)
