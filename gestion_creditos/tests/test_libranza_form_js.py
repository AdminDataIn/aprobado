import re
from pathlib import Path

from django.test import SimpleTestCase


class LibranzaFormJavaScriptTests(SimpleTestCase):
    template_path = Path("templates/gestion_creditos/solicitud_libranza.html")

    def _template_source(self):
        return self.template_path.read_text(encoding="utf-8")

    def test_inline_handlers_reference_global_functions(self):
        source = self._template_source()
        inline_calls = set(re.findall(r'on\w+="([A-Za-z_$][\w$]*)\(', source))
        global_functions = set(re.findall(r"^\s*function\s+([A-Za-z_$][\w$]*)\(", source, re.MULTILINE))

        self.assertEqual({"nextStep", "previousStep"}, inline_calls)
        self.assertTrue(inline_calls.issubset(global_functions))

    def test_next_step_dependencies_are_exported_to_global_scope(self):
        source = self._template_source()

        self.assertIn("window.validateLibranzaField = validateLibranzaField;", source)
        self.assertIn("window.mostrarError = mostrarError;", source)
        self.assertIn("window.limpiarError = limpiarError;", source)

    def test_form_uses_url_name_simulador_no_legacy_simulacion(self):
        source = self._template_source()

        self.assertIn("libranza:simulador", source)
        self.assertNotIn("libranza:simulacion", source)

    def test_company_selection_uses_delegated_pointer_event(self):
        source = self._template_source()

        self.assertIn("event.target.closest('.company-search-option')", source)
        self.assertIn("const selectionEvent = window.PointerEvent ? 'pointerdown' : 'mousedown';", source)
        self.assertIn(
            "empresaResultados.addEventListener(selectionEvent, seleccionarEmpresaDesdeEvento);",
            source,
        )
        self.assertIn("event.preventDefault();", source)
        self.assertNotIn("window.setTimeout(limpiarResultadosEmpresa, 180);", source)

    def test_company_selection_persists_id_and_keyboard_support(self):
        source = self._template_source()

        self.assertIn("empresaHiddenInput.value = empresa.id;", source)
        self.assertIn("empresaBusquedaInput.value = empresa.razon_social || empresa.nombre;", source)
        self.assertIn("empresaHiddenInput.value = '';", source)
        self.assertIn("if (event.detail === 0)", source)
