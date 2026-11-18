# Archivo operativo auditado de Prestadores

El borrado fisico del lote continua bloqueado y su comando no forma parte del release.
El archivo no es rechazo, cancelacion, revocacion de autorizacion,
anonimizacion ni supresion. No se ha ejecutado sobre produccion.

## Contrato funcional

- `ArchivoSolicitudPrestador` guarda una sola marca por solicitud, con actor protegido,
  fecha y motivo obligatorio. No se reescribe el estado ni la version de la solicitud.
  El reenvio autorizado devuelve la misma marca y conserva la evidencia original.
- Admin de Django: seleccione una sola solicitud, ejecute **Archivar una solicitud**
  e indique el motivo en la confirmacion POST. Requiere staff activo sin perfil pagador,
  permiso `contractors.can_archive_contractor_application` y acceso de lectura al modelo.
  El servicio comprueba bloqueo y permiso de nuevo. No hay accion de desarchivo.
- Una solicitud archivada tampoco puede usar el simulador informativo del
  calendario flexible. Las reglas y el parser del calendario no cambian.
- Bandejas normales, Mi credito, aprobaciones del pagador y continuidad excluyen archivos.
  Admin autorizado puede consultar **Archivo operativo > Archivadas** o **Todas**, asi
  como el detalle interno. No se habilita edicion de un expediente archivado.
- El control de continuidad existente opera por titular y estados en curso, no por
  unicidad de documento en Prestadores. La misma cuenta y documento pueden iniciar un
  expediente nuevo si no hay otra solicitud activa que lo impida. No se modifican
  reglas de identidad, credito, restricciones por empresa ni de otros productos.
- No se permite originar desde un archivo ni reutilizar su autorizacion, snapshot
  (incluidos errores) o decision para una solicitud nueva. Una solicitud nueva requiere
  su consentimiento y su propia evaluacion; no cambia la politica de consultas.
- El archivo se bloquea ante credito del usuario, coincidencia documental exacta con
  detalle financiero, origen por `gate_id`, estado de firma o referencias financieras
  en timeline. Tambien ante jobs, evaluaciones, reservas DataCredito u OCR no resueltos.
  Revisiones y decisiones compatibles se conservan sin cambiar sus estados. La purga
  automatica omite evidencia de captura asociada al archivo.

## Procedimiento de despliegue para revision

1. Verificar que el HEAD instalado y el remoto siguen en el padre acreditado
   `ad623289954981a40058613d0792db6dd666c07d`. Autorizar por separado la
   publicacion y actualizacion fast-forward del SHA exacto del candidato. Excluir el
   comando previo de borrado y los cambios locales de otros frentes. Confirmar respaldo
   restaurable (codigo, configuracion y PostgreSQL, con restauracion de prueba aislada)
   y ventana sin archivados ni despliegues concurrentes. No habilitar flags.
2. Confirmar que las tareas de evaluacion y OCR/purga no estan en curso, registrar
   cuales unidades estan activas y drenar Gunicorn y los workers afectados antes
   de cambiar el checkout. No iniciar unidades que estaban inactivas. En el VPS,
   traer solo la rama de release, verificar ambos SHAs y el padre directo;
   actualizar por fast-forward al candidato autorizado.
3. En el candidato, ejecutar `venv/bin/python manage.py migrate contractors 0023 --plan`.
   Detenerse si el plan incluye algo distinto de crear `ArchivoSolicitudPrestador`.
   Aplicar `venv/bin/python manage.py migrate contractors 0023` antes de arrancar
   los procesos que consultan la marca. Es una migracion aditiva: tabla, relaciones,
   unicidad y permiso; no archiva solicitudes ni reescribe historicos.
   Comprobar `venv/bin/python manage.py check` y `showmigrations contractors`.
   Renovar solo las unidades previamente activas que sirven la app o ejecutan
   evaluacion y OCR/purga, sin cambiar Redis, Nginx ni flags; verificar salud HTTPS
   de Libranza/Prestadores y calendario sintetico sin escrituras productivas.
   No abrir el archivo operativo con procesos antiguos vivos. Conceder el permiso
   de archivo solo al actor aprobado, separado de la aprobacion para archivar el lote.
   Corroborar que el despliegue no creo marcas y que los servicios no reportan errores
   de esquema, comparando conteos agregados y salud antes y despues.
4. Obtener la PK real de `PRUEBAS DATAIN` y el actor autorizado. Con rol de BD de lectura,
   ejecutar el preflight. No existe modo de escritura en este comando:

   ```bash
   python manage.py preflight_archivo_pruebas_datain --empresa-id "$EMPRESA_ID" --actor-id "$ACTOR_ID"
   ```

5. Conciliar cada ID y bloqueo. Independientemente del resultado `ELEGIBLE`, requerir
   aprobacion operativa separada antes de archivar desde el admin. El procedimiento
   termina en lectura en esta tarea; no asignar permisos ni archivar ahora.

**Contingencia:** si aun no hay marcas, puede revertirse el codigo conservando la
tabla. Si ya hay marcas, no arrancar versiones que las ignoren: corregir hacia adelante
o mantener inhabilitadas las operaciones afectadas durante la recuperacion. No revertir
`0023` sobre registros archivados ni eliminar marcas para simular una reversa.

## Inventario del cambio

- Esquema y servicio: `contractors/models.py`, `contractors/migrations/0023_archivosolicitudprestador.py`,
  `contractors/services/archivo_solicitud.py` y `contractors/management/commands/preflight_archivo_pruebas_datain.py`.
- Superficies: `contractors/admin.py`, `contractors/views.py`, `contractors/views_admin.py`,
  `contractors/views_pagador.py`, `templates/admin/contractors/confirmar_archivo.html` y
  `templates/contractors/admin_detalle_prestador.html`.
- Fronteras operativas en `contractors/services/`: `aprobacion_interna.py`, `aprobacion_pagador.py`,
  `autorizacion_datacredito.py`, `confirmacion_simulacion.py`, `continuidad_solicitud.py`,
  `datacredito_evaluacion.py`, `decision_definitiva.py`, `evaluacion_audit.py`, `evaluacion_formal.py`,
  `ingreso_neto.py`, `oferta_financiera.py`, `orquestacion_evaluacion.py`, `originacion.py`,
  `politica_ingreso_publicacion.py`, `revision_manual.py`, `solicitud.py` y `subsanacion.py`.
- Integraciones: `gestion_creditos/services/captura_documental.py`, `ocr_documental.py` y
  `originacion_libranza.py` (solo punto de entrada del expediente Prestadores).
- Pruebas en `contractors/tests/`: `test_archivo_solicitud.py`, `test_reutilizacion_snapshots.py`,
  `test_portal_minimo_prestadores.py`, `test_pipeline_financiero_e2e.py`.
