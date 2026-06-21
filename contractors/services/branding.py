from contractors.selectors import obtener_branding_activo_por_organizacion


COLOR_PRIMARIO_DEFAULT = '#0d6efd'
COLOR_SECUNDARIO_DEFAULT = '#6c757d'
NOMBRE_VISUAL_DEFAULT = 'Aprobado'
TEXTO_LANDING_DEFAULT = ''


def obtener_contexto_branding_con_defaults(organizacion):
    if hasattr(organizacion, 'nombre_visible') and hasattr(organizacion, 'color_primario'):
        return {
            'organizacion_id': None,
            'configuracion_portal_id': getattr(organizacion, 'id', None),
            'nombre_visual': organizacion.nombre_visible or NOMBRE_VISUAL_DEFAULT,
            'url_logo': organizacion.logo.url if organizacion.logo else '',
            'color_primario': organizacion.color_primario or COLOR_PRIMARIO_DEFAULT,
            'color_secundario': organizacion.color_secundario or COLOR_SECUNDARIO_DEFAULT,
            'correo_soporte': organizacion.correo_soporte or '',
            'texto_landing': organizacion.texto_landing or TEXTO_LANDING_DEFAULT,
            'tiene_branding_personalizado': True,
        }

    branding = obtener_branding_activo_por_organizacion(organizacion)
    if not branding:
        return _contexto_default(organizacion)

    return {
        'organizacion_id': getattr(organizacion, 'id', None),
        'nombre_visual': branding.display_name or _nombre_organizacion(organizacion),
        'url_logo': branding.logo.url if branding.logo else '',
        'color_primario': branding.primary_color or COLOR_PRIMARIO_DEFAULT,
        'color_secundario': branding.secondary_color or COLOR_SECUNDARIO_DEFAULT,
        'correo_soporte': branding.support_email or '',
        'texto_landing': branding.landing_copy or TEXTO_LANDING_DEFAULT,
        'tiene_branding_personalizado': True,
    }


def _contexto_default(organizacion):
    return {
        'organizacion_id': getattr(organizacion, 'id', None),
        'nombre_visual': _nombre_organizacion(organizacion),
        'url_logo': '',
        'color_primario': COLOR_PRIMARIO_DEFAULT,
        'color_secundario': COLOR_SECUNDARIO_DEFAULT,
        'correo_soporte': '',
        'texto_landing': TEXTO_LANDING_DEFAULT,
        'tiene_branding_personalizado': False,
    }


def _nombre_organizacion(organizacion):
    return getattr(organizacion, 'name', None) or NOMBRE_VISUAL_DEFAULT


# Alias temporal de compatibilidad.
get_branding_context_with_defaults = obtener_contexto_branding_con_defaults
