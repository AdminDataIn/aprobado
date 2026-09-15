# ID-01B: handoff documental

El componente compartido conserva endpoints, permisos, CSRF, estados, hash,
token en fragmento y TTL de ID-01A. No crea credito al generar el enlace.
No envia email. No acredita identidad por disponer de una imagen o por detectar
un dispositivo movil. La deteccion de pantalla solo adapta la presentacion.

## Comportamiento

- Storage opcional: solo UUID en sessionStorage, nunca token ni enlace.
- El formulario permanece en su pestana; Abrir captura abre otra. No se promete
  recuperar datos tras cerrar/recargar si el navegador bloquea almacenamiento.
- Reload con UUID consulta estado. Solo un click explicito sobre una sesion
  ABIERTA sin enlace llama regenerar. EXPIRADA/REVOCADA requieren crear otra.
- CANJEADA espera finalizacion; FINALIZADA/UTILIZADA ocultan y limpian el enlace.
- Polling cada 5 s, maximo 120 consultas por ciclo; dos reintentos automaticos
  adicionales ante errores transitorios, con pausa creciente. Consulta manual
  disponible. No se repiten automaticamente los POST.
- Timeout de requests: 15 s. Ante una respuesta perdida de crear, un nuevo click
  puede crear otro borrador; no crea obligaciones ni efectos financieros.
- Los errores del backend no se imprimen literalmente ni se registran en JS.
- Se requiere publicar ambos assets CSS/JS y la dependencia QR local en el
  despliegue habitual de staticfiles. Este bloque no cambia infraestructura.

## QR local

La tarjeta no repite el logo. El indicador de espera es un spinner pequeno con
texto aria-live; deja de animarse al finalizar, fallar o pausarse el polling.
Respeta prefers-reduced-motion.

`static/js/vendor/qrcode-generator-1.4.4.js`: copia sin modificar de qrcode.js,
paquete npm qrcode-generator 1.4.4, Kazuhiko Arase, MIT (licencia junto al archivo).
Fuente: https://github.com/kazuhikoarase/qrcode-generator

No existia dependencia QR en requirements ni assets. Se incluye solamente este
archivo, sin bundler ni dependencias runtime adicionales. Genera una matriz QR
automaticamente dimensionada, correccion M y margen blanco de cuatro modulos.
Canvas codifica el enlace absoluto same-origin devuelto por backend, incluido
su fragmento. No hay fetch de imagen ni servicio externo. Si falta la libreria,
Abrir captura y Copiar enlace siguen disponibles.

## Camara compartida Libranza / Prestadores

`captura_continuacion.html` y el controlador movil de `captura_documental.js`
usan el mismo flujo para ambos productos, sin bifurcar permisos ni servicios:

1. Abrir camara consulta estado y canjea el token mediante el endpoint existente.
2. getUserMedia pide solo video, facingMode environment como preferencia.
3. Visor con guia visual -> Tomar foto -> preview -> Repetir / Usar esta foto.
4. Canvas produce un Blob JPEG (calidad .92, lado maximo 2048 px, sin recortar).
5. POST multipart `archivo=captura.jpg` a FRONTAL y despues TRASERA.
6. Finalizar llama el endpoint existente. Solo la respuesta terminal confirma exito.

No hay file picker, galeria ni fallback automatico. No se almacena imagen/base64
en storage ni se imprimen secretos. Solo existe Blob en memoria y object URL
temporal para preview; se revoca al repetir, aceptar, terminar o abandonar.
El backend conserva su validacion/reprocesado y almacenamiento privado.

Los tracks se detienen al fotografiar, abandonar, ocultar la pestana, finalizar
y ante errores. Una concesion tardia de permiso tampoco deja tracks activos.
Si no responde el dialogo de permisos en 30 s, la interfaz permite reintentar;
getUserMedia no tiene cancelacion nativa, por lo que cualquier stream tardio se
descarta y detiene. No se reabre camara automaticamente al volver a la pestana.

Estado se consulta cada 10 s durante captura visible y antes de abrir. Ante
EXPIRADA/REVOCADA se cierran camara/preview sin regeneracion automatica.
Cada subida/finalizacion sigue autorizada por el backend, no por el polling.
Un error de upload conserva la foto para reintento explicito. No se repite el
canje ni el POST automaticamente. Una respuesta perdida puede provocar reenvio
del mismo Blob: se conserva la idempotencia por hash ya existente en el servicio.

## Navegadores y limites

- Requiere contexto seguro HTTPS (localhost es excepcion de desarrollo), permiso
  y camara disponible. No usar una IP HTTP de red local para validar en celular.
- Safari iOS y Chrome Android compatibles deben mostrar el permiso y preferir
  camara trasera. `ideal` no garantiza una lente concreta. Video muted/playsinline
  evita exigir audio o presentacion a pantalla completa.
- Navegadores embebidos, politicas del dispositivo, permiso bloqueado o camara
  ocupada pueden impedir la captura; se muestra un mensaje, no carga manual.
- Desktop con camara tambien puede usar el flujo. Viewport/touch/userAgent no
  son controles de autorizacion. No hay bloqueo de DevTools.
- No es prueba de vida, autenticidad del documento ni deteccion de camara virtual.
  La nitidez requiere revision del usuario; la guia no detecta bordes ni hace OCR.
- Pendiente UAT con Safari/iPhone y Chrome/Android fisicos, permisos reales,
  rotacion, cambio de app, conectividad y dos sesiones autenticadas.

Referencia de la API y restricciones:
https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia

## Validacion reproducible

Django (usar entorno de pruebas, nunca datos productivos):

```bash
python manage.py test gestion_creditos.tests.test_captura_documental gestion_creditos.tests.test_documentos_privados contractors.tests.test_portal_minimo_prestadores gestion_creditos.tests.test_originacion_libranza gestion_creditos.tests.test_integridad_originacion_anulacion --verbosity 2
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
```

Browser: Node 22+ y herramientas de desarrollo fuera del repositorio. Ejemplo
PowerShell desde la raiz; no son dependencias de la aplicacion productiva:

```powershell
npm.cmd install --prefix "$env:TEMP/aprobado-id01b-tools" --ignore-scripts --no-audit --no-fund @playwright/test@1.55.0 jsqr@1.4.0
$env:NODE_PATH="$env:TEMP/aprobado-id01b-tools/node_modules"
$env:PLAYWRIGHT_BROWSERS_PATH="$env:TEMP/aprobado-id01b-browsers"
& "$env:TEMP/aprobado-id01b-tools/node_modules/.bin/playwright.cmd" install chromium
node --test gestion_creditos/tests/browser/test_captura_handoff.cjs
```

En Linux instalar los mismos paquetes en /tmp y exportar NODE_PATH y
PLAYWRIGHT_BROWSERS_PATH. DJANGO_PYTHON permite elegir el interprete de Django.
HANDOFF_SCREENSHOTS permite guardar capturas fuera del repositorio.

Los tests usan el canal Chromium completo en headless, no chromium-headless-shell
(este ultimo devolvio NotSupportedError para getUserMedia en Windows).
https://playwright.dev/docs/browsers

Los tests browser sirven el template Django y assets reales con endpoints HTTP
simulados y un token ficticio. Validan QR mediante decodificacion independiente
jsQR, storage denegado, CSRF, errores, reintentos, terminales, reload, responsive
y ausencia de token en logs/storage/requests externos. No sustituyen UAT con
dos dispositivos autenticados ni pruebas PostgreSQL de concurrencia.
La camara automatizada es el dispositivo sintetico de Chromium: getUserMedia,
MediaStream, video, canvas, JPEG y preview funcionan realmente en el browser,
sin usar documentos ni camaras personales. Errores de permisos/dispositivo se
inyectan en pruebas. Se verifican cierre de tracks, ambos productos, reintentos,
secuencia de caras, multipart y responsive 320/768/1280.
