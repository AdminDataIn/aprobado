"""Unchanged 'centrales' content from legal_prestadores_view, July 2026.

Source dictionary SHA256 (JSON UTF-8, sorted keys):
f40d1f8b3bcf5397874b34528070e7f849790e61aef21ed6e81f8b5ea61f90e3.
P0-5B2.2B2 authorizes reuse, not rewriting its legal clauses.
"""

VERSION_CONSENTIMIENTO_CENTRALES = 'prestadores-centrales-v1'
RESUMEN_CONSENTIMIENTO_CENTRALES = (
    'Autorizo la consulta futura ante centrales de información y riesgo en etapas posteriores del proceso.'
)
CONTENIDO_CENTRALES = {
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
}

# Plain-text presentation includes the complete existing content, not only the CTA.
TEXTO_CONSENTIMIENTO_CENTRALES = '\n\n'.join((
    CONTENIDO_CENTRALES['titulo'], CONTENIDO_CENTRALES['actualizacion'],
    CONTENIDO_CENTRALES['introduccion'],
    *('\n'.join((numero + '. ' + titulo, texto))
      for numero, titulo, texto in CONTENIDO_CENTRALES['secciones']),
    CONTENIDO_CENTRALES['destacado'],
))
