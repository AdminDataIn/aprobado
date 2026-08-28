import os
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateDocumentStorage(FileSystemStorage):
    """Almacenamiento local sin URL pública para documentos institucionales."""

    def __init__(self):
        super().__init__(location=None, base_url=None)

    @property
    def base_location(self):
        return str(getattr(
            settings,
            'PRIVATE_DOCUMENTS_ROOT',
            Path(settings.BASE_DIR) / 'private_documents',
        ))

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError('Los documentos privados no tienen una URL pública.')


private_document_storage = PrivateDocumentStorage()
