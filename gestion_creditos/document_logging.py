import logging
import re


class OcultarTokenPagare(logging.Filter):
    """Tambien sanea los mensajes automaticos 4xx de django.request/server."""
    def filter(self, record):
        mensaje = record.getMessage()
        if '/api/pagares/download/' in mensaje:
            record.msg = re.sub(r'/api/pagares/download/[^\s/\"\'?]+',
                                '/api/pagares/download/[REDACTED]', mensaje)
            record.args = ()
        return True
