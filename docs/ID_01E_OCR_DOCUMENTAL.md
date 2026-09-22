# ID-01E - OCR documental local

## Alcance y limites

OCR extrae texto; no verifica identidad, autenticidad, presencia fisica ni liveness.
No actualiza persona, solicitud, credito, score, aprobacion ni contabilidad.
No ofrece endpoints nuevos ni amplia CaptureGrant. Camara y quality gate intactos.
El gate es UX cliente (incluye revision manual), no una atestacion del servidor.

## Flujo y consistencia

`_finalizar_sesion` crea un procesamiento PENDIENTE en la transaccion existente.
`on_commit` publica solo su ID en Celery. La captura HTTP nunca ejecuta Tesseract.
La fila pendiente es el registro durable si no hay broker/worker.
El worker reserva con `select_for_update`, UUID de ejecucion y lease de
`2 * (timeout_por_cara + 15) + 60` segundos. Orden: sesion, contexto vinculado,
procesamiento. OCR corre fuera de transaccion. Al publicar se comprueban token,
estado, hash de inputs, capturas activas y vigencia de sesion/contexto.

Los resultados terminales nunca vuelven a ejecutarse por tarea duplicada.
Solo OCR_TIMEOUT/OCR_ERROR_TECNICO se reintentan automaticamente, maximo tres
ejecuciones. No se reintentan discrepancias, desconocidos ni motor no instalado.
Una reserva vencida permite recuperar un worker muerto; su resultado tardio no
puede sobreescribir la ejecucion nueva. No se promete exactly-once del computo:
una caida puede requerir repetir OCR, pero solo una publicacion es vigente.

La firma fija IDs/hashes de ambas caras y la configuracion fija version exacta
del motor, SHA256 de spa.traineddata, parser, idioma, OEM/PSM y timeout.
Versionar parser/configuracion cuando cambien reglas/umbrales.
No se reprocesan documentos historicos automaticamente.

`sincronizar_comparacion_ocr` se llama al terminar OCR y al consumir documentos:
resuelve ambos ordenes sin duplicar. Solo compara contexto persistido UTILIZADA,
nunca el username. Libranza: cedula/nombres/apellidos del detalle; Prestadores:
numero_documento/nombres/apellidos/tipo_documento de ContractorApplication.
Las modificaciones posteriores de datos pueden sincronizarse explicitamente
con ese mismo servicio; no se instalan signals sobre modelos financieros.
Subsanacion ya llama `consumir_documentos`: la sesion nueva desplaza la vigencia
de resultados/comparaciones anteriores, conservando su evidencia historica.

## Parser v1

`cc-etiquetas-1` soporta exclusivamente el contrato textual sintetico probado:
cabeceras REPUBLICA DE COLOMBIA / CEDULA DE CIUDADANIA, campos rotulados
NUMERO, NOMBRES, APELLIDOS en frontal; fechas rotuladas en trasera.
Etiquetas y valores pueden estar en una linea separada o separados por dos puntos.
No certifica soporte para todos los layouts fisicos (amarilla/digital/variantes).
Si el reverso no expone esas anclas, queda DESCONOCIDO/revision, no inventa campos.
CE es NO_SOPORTADO; si el contexto declara CE, comparacion NO_DISPONIBLE.
Umbral inicial conservador: confianza minima de linea 70 para usar un campo.
No es probabilidad de identidad; requiere calibracion antes de apertura amplia.
Solo fechas ISO inequívocas en v1; formatos ambiguos se remiten a revision.
No corrige O/0 ni I/1. Nombres: espacios/mayusculas/tildes, sin fuzzy.
Se conserva solo cada campo bruto/normalizado, confianza y codigos/layout;
no el texto OCR completo ni bounding boxes de todo el documento.

## Privacidad y retencion

Entrada exclusivamente `captura.archivo.open('rb')` y hash verificado.
Tesseract usa un subprocess Python aislado para que sus temporales queden en
`PRIVATE_DOCUMENTS_ROOT/ocr_temporales/<directorio-aleatorio>` con modo 0700
en Linux, sin cambiar tempfile global del worker/banco. Copia efimera de entrada
y outputs se eliminan al salir incluso si falla OCR; no se persisten imagenes nuevas.
No hay URL publica. No registrar stdout/stderr del subprocess: pueden contener PII.
Logs/eventos/tareas solo IDs y codigos. No se registra el modelo en admin generico.

La purga existente elimina campos OCR/snapshots de sesiones abandonadas expiradas;
conserva filas tecnicas/eventos. Invalida tokens de ejecucion para impedir
republicacion por un worker en vuelo. No purga UTILIZADA por TTL del grant.
Resultados finales vinculados mantienen la retencion documental existente.
Los snapshots comparados contienen PII: solo acceso interno documental autorizado;
no exponerlos en polling, serializaciones generales, correos ni APIs publicas.

SIGKILL/corte electrico no ejecuta finally: el operador debe revisar temporales
abandonados solo cuando no haya workers activos; no borrar documentos originales.
El usuario del worker requiere lectura del storage privado y escritura de temporales.

## Validacion VPS confirmada

Validacion comunicada por el responsable: PostgreSQL validado, Tesseract 5.3.4,
`pytesseract==0.3.13`, `spa.traineddata` validado y OCR sintetico real probado.
SHA256 del modelo validado:

```text
6f2e04d02774a18f01bed44b1111f2cd7f3ba7ac9dc4373cd3f898a40ea6b464
```

Esta evidencia no equivale a calibracion fisica ni a verificacion de identidad.
Los tests locales pueden omitir PostgreSQL/motor real si no estan disponibles;
eso no reemplaza ni repite la validacion VPS reportada.

## Preparacion VPS (sin instalar/desplegar desde este ticket)

En el entorno de pruebas autorizado, con su venv y base clon:

```sh
tesseract --version
tesseract --list-langs
```

Requisitos: Tesseract 5, modelo espanol spa y dependencias nativas Leptonica.
`pytesseract==0.3.13` ya esta declarado; Pillow ya se utiliza en captura.
No requiere pdf2image/Poppler para identidad. No descarga modelos en runtime.
Motor/modelos open source; revisar avisos Apache-2.0 de Tesseract/pytesseract.

Configurar antes de habilitar el worker con estos trabajos:

- `OCR_DOCUMENTAL_VERSION_MOTOR`: version exacta devuelta por pytesseract.
- `OCR_DOCUMENTAL_TESSDATA_DIR`: directorio privado/solo lectura de modelos.
- `OCR_DOCUMENTAL_MODELO_SHA256`: SHA256 aprobado de spa.traineddata.
- `OCR_DOCUMENTAL_TIMEOUT`: segundos por cara, default 20, limitado a 1..60.
- Tesseract en PATH (o `settings.TESSERACT_CMD` existente en la instalacion).

Version/hash vacios o distintos no se aceptan silenciosamente. Queda FALLIDO
con codigo seguro; configurar correctamente y crear nueva version con
`asegurar_procesamiento(sesion_id)` para una sesion valida explicitamente elegida.
No modificar ni reencolar en masa filas terminales para forzar reprocesamiento.
No se ha medido precision, latencia, CPU/RAM del VPS: usar fixtures sinteticos y
benchmark con concurrencia inicial 1 antes de aumentar trabajadores.

## Worker OCR dedicado requerido en produccion

El routing centralizado en `CELERY_TASK_ROUTES` envia exclusivamente
`gestion_creditos.tasks.procesar_ocr_documental_task` a `ocr_documental`.
La publicacion inicial, reintentos y `reencolar_ocr_documental` usan el mismo
publicador y routing; no se fijan colas en los servicios.
Las demas tareas conservan la cola default actual (`celery` en esta configuracion).

Comando recomendado, desde el proyecto y con el venv/configuracion productivos:

```sh
celery -A aprobado_web worker \
  --loglevel=info \
  -Q ocr_documental \
  --concurrency=1 \
  -n ocr_documental@%h
```

El worker general debe continuar consumiendo exclusivamente su cola default;
verificar su lista de colas y usar `-Q celery` si es necesario explicitarla.
No debe suscribirse a `ocr_documental`. El worker OCR solo consume esa cola.
Concurrencia inicial 1; cualquier aumento requiere medicion previa de CPU, RAM
y latencia con carga representativa. El routing por si solo no arranca workers.
Sin worker dedicado, los mensajes OCR permanecen pendientes en su cola.

Al adoptar esta configuracion, comprobar si quedan mensajes OCR antiguos en la
cola general: el routing no mueve mensajes ya publicados. No purgar esa cola,
pues contiene otras tareas. Coordinar su drenaje antes de declarar aislamiento.
No se crean/modifican servicios systemd ni se reinician procesos desde este ticket.

## Recuperacion y validacion

```sh
python manage.py reencolar_ocr_documental --limite 100
python manage.py test gestion_creditos.tests.test_ocr_documental gestion_creditos.tests.test_ocr_tesseract --verbosity 2
python manage.py test gestion_creditos.tests.test_ocr_routing --verbosity 2
python manage.py test gestion_creditos.tests.test_ocr_documental.OCRConcurrenciaPostgresTest --verbosity 2
python manage.py test gestion_creditos.tests.test_captura_documental gestion_creditos.tests.test_capture_grant gestion_creditos.tests.test_documentos_privados --verbosity 1
```

La recuperacion bloquea filas, limita lote a 1000 y aplica lease de publicacion
de 60 s para evitar inundacion por comandos concurrentes. Si broker sigue caido,
repetir despues del lease. No instala schedule Beat.
Pruebas deterministas usan adaptador simulado; el test real se omite si no existe
binario/spa/ruta de modelo configurada, con motivo explicito. Solo datos sinteticos.
Conservar las pruebas PostgreSQL de reservas, doble creacion, doble tarea y recuperacion.
Conservar regresiones de grant/CSRF, privacidad, Libranza y Prestadores.

## Pendientes de cierre

Calibracion fisica y ampliacion aprobada de layouts, puesta en marcha/verificacion
del worker dedicado, UAT Android y revision operativa. iPhone fue validado por el responsable
para captura, no constituye validacion OCR real. UX OCR futura no implementada.
Despues retomar Prestadores E2E, BRE-B V1/UAT restante y WhatsApp tras estabilizacion.
BRE-B-ALERT-01 desplegado segun responsable, pendiente proximo reporte real;
BRE-B-CLOSE-01 y BRE-B-GUARD-01 siguen en roadmap. Antifraude/liveness fuera de alcance.
