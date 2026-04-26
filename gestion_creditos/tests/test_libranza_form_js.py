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
