from django import template
from gestion_creditos.services.documentos_privados import url_documento

register = template.Library()
register.simple_tag(url_documento, name='documento_url')
