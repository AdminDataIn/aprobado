import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.files.storage import Storage, default_storage
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


@deconstructible
class DocumentosSolicitudStorage(Storage):
    """Lee referencias legacy sin moverlas; escribe cedulas nuevas en privado."""

    prefix = 'identidad/'

    def _backend(self, name):
        return private_document_storage if name.startswith(self.prefix) else default_storage

    def _open(self, name, mode='rb'):
        return self._backend(name).open(name, mode)

    def _save(self, name, content):
        identidad = name.startswith(self.prefix) or '/cedulas/' in name or Path(name).stem.startswith('CEDULA_')
        if identidad:
            name = f'{self.prefix}{uuid.uuid4().hex}{Path(name).suffix.lower()}'
            return private_document_storage.save(name, content)
        return default_storage.save(name, content)

    def exists(self, name):
        return self._backend(name).exists(name)

    def size(self, name):
        return self._backend(name).size(name)

    def path(self, name):
        return self._backend(name).path(name)

    def url(self, name):
        return self._backend(name).url(name)

    def delete(self, name):
        return self._backend(name).delete(name)


documentos_solicitud_storage = DocumentosSolicitudStorage()
