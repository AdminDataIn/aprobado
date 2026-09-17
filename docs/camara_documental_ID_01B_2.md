# ID-01B.2: camara documental compartida

## Alcance

Presentacion/captura en captura_continuacion.html y captura_documental.js/css,
compartida por Libranza, Prestadores y subsanacion. No cambia CaptureGrant,
CSRF, tokens, TTL, cookies, endpoints, servicios, storage ni estados backend.
No requiere migracion. No hay input file, galeria, OCR ni validacion de identidad.

## Visor y encuadre

Antes de abrir: introduccion compacta. Durante apertura/video/preview: vista
dedicada de altura 100dvh (100vh como fallback), safe-area-inset, salida y paso
arriba, disparador o repetir/aceptar abajo. En horizontal, acciones laterales.
Salir apaga la camara y vuelve al inicio local, sin revocar ni alterar el grant.

El video conserva object-fit: contain y object-position: center. Para un box CSS
Bw x Bh y resolucion intrinseca Vw x Vh:

- escala = min(Bw/Vw, Bh/Vh)
- area visible = (Vw*escala, Vh*escala), centrada dentro del box
- ratio ID-1 = 85.60/53.98
- ancho marco = min(ancho visible*0.82, alto visible*0.72*ratio)
- alto marco = ancho marco/ratio

No se usan barras de letterboxing como area del documento. loadedmetadata,
resize del video/ventana, ResizeObserver, visualViewport y orientationchange
recalculan el marco. No se recorta la foto al marco: es una guia visual.
Si la camara mantiene un stream vertical tras rotar el dispositivo, el visor
lo respeta; no inventa un encuadre horizontal ni estira la imagen.

## Captura, resolucion y capacidades

getUserMedia pide environment ideal, 1920x1080 ideal y audio=false. Settings y
capabilities reales quedan solo en memoria mientras vive el stream.

1. ImageCapture.takePhoto cuando existe y funciona, con espera maxima de 6 s.
2. Se decodifica la foto. JPEG/PNG/WebP <=8 MiB y <=20 MP se envian sin
   recomprimir en el cliente, conservando resolucion y formato nativo.
3. Si supera limites o requiere otro formato, se codifica JPEG a calidad 0.95,
   limitando primero a 20 MP. Si supera 8 MiB se reduce progresivamente desde
   la fuente original, no desde JPEG sucesivos (maximo seis intentos).
4. Si la captura nativa no existe/falla/no puede decodificarse, canvas usa
   videoWidth/videoHeight, no dimensiones CSS. Se aplican los mismos limites.
   No existe limite fijo de 2048 ni ampliacion artificial de resolucion.

El backend preexistente sigue reorientando/limpiando metadata y convirtiendo
a JPEG calidad 95. Esa recodificacion de servidor sigue siendo inevitable;
no se ha modificado. El servidor conserva la ultima palabra sobre limites.

Zoom solo aparece si capabilities.zoom es un rango valido y applyConstraints
existe. Se ajusta a min/max/step reales mediante advanced.zoom, nunca CSS scale.
El rango nativo puede representar zoom digital del dispositivo, no se promete
zoom optico. Foco continuo solo se intenta si figura entre las capacidades.
El fallo de estas mejoras no bloquea captura. No se agrega torch en esta fase:
es opcional y puede aumentar reflejos; autofocus del navegador sigue disponible.

Se detienen tracks en captura, salida, repeticion/reinicio, errores, expiracion,
revocacion, finalizacion, pagehide y pestaña oculta. Un contador de generacion
descarta streams y fotos tardios. Se liberan canvas y URLs temporales.
Upload fallido conserva la foto para reintentar sin nuevo canje.

## Desktop

Sin CTA visible para abrir la camara del PC: QR, copiar enlace, expiracion y
espera/finalizacion. La opcion de abrir directamente solo aparece con layout
movil/coarse pointer. Es UX, no control de seguridad ni bloqueo por userAgent.
Una URL abierta manualmente en desktop mantiene el flujo permitido por el grant.

## Cache y publicacion futura

Ambos templates referencian JS y CSS con ?v=id01b2-1. Cambiar ambos valores en
ambos templates ante futuros cambios de estos assets; hay prueba de consistencia.
QR no cambio: js/vendor/qrcode-generator-1.4.4.js ya versiona su nombre.

La configuracion historica declara STATICFILES_STORAGE, pero el backend efectivo
local en Django 5.2 es StaticFilesStorage; no se asume manifest/hash efectivo.
No se modifico la estrategia global de static ni Nginx. El querystring evita
reusar la URL anterior con cache immutable, si el proxy conserva la query como
parte de la clave de cache (comprobarlo en el entorno real).

En un despliegue autorizado posterior, con templates y assets de la misma version:

```bash
python manage.py check
python manage.py collectstatic --noinput
```

Verificar que las URLs ?v=id01b2-1 devuelvan estos assets, sin 404, y que el HTML
publicado referencia esa version. No basta recargar HTML si staticfiles sigue
conteniendo archivos anteriores. No se ejecuto collectstatic ni deploy aqui.

## Validacion

- Browser Chromium con camara sintetica y capacidades controladas: visor,
  geometria/rotacion, resolucion intrinseca, ImageCapture/fallback, limites,
  zoom/foco opcionales, errores, lifecycle, 403 grant, ausencia de secretos,
  sin galeria y desktop sin CTA. Screenshots y lectura de pixeles del stream.
- HTTPS contra Django real en DB temporal: PC y movil con contextos aislados,
  CSRF, reload, frontal/posterior, finalizacion detectada por PC.
- Suite Django: captura, grant, documentos privados, contractors completo,
  originacion Libranza e integridad de originacion/anulacion.
- PostgreSQL se omite explicitamente donde no esta disponible.

```powershell
$env:NODE_PATH="$env:TEMP/aprobado-id01b-tools/node_modules"
$env:PLAYWRIGHT_BROWSERS_PATH="$env:TEMP/aprobado-id01b-browsers"
node --test gestion_creditos/tests/browser/test_captura_handoff.cjs
$env:RUN_CAPTURE_GRANT_BROWSER='1'
python manage.py test gestion_creditos.tests.test_capture_grant_browser
```

## UAT fisico pendiente

No confundir emulacion de viewport/stream con un iPhone o Android real.
En clon HTTPS, con ID-01B.1 disponible, repetir Libranza y Prestadores:

1. PC genera QR; telefono sin login abre y canjea normalmente.
2. Safari iPhone vertical y horizontal: esquinas dentro del marco, controles
   visibles con barras del navegador, girar antes/despues de abrir camara.
3. Android Chrome: captura nativa si existe; comparar legibilidad del preview.
4. Zoom/foco solo segun capacidades reales; su ausencia nunca debe bloquear.
5. Tomar/repetir/aceptar frontal, recargar, posterior/finalizar y confirmar PC.
6. Denegar permiso, volver de background, expirar/revocar desde PC y reintentar
   upload fallido; ninguna camara debe quedar activa fuera del visor.

Safari se beneficia de playsinline/muted y tiene fallback canvas sin depender
de ImageCapture. Chrome tambien usa deteccion real; marca/version no garantizan
la disponibilidad de takePhoto, zoom ni autofocus controlable.

Fuentes tecnicas: [ImageCapture](https://developer.mozilla.org/en-US/docs/Web/API/ImageCapture/takePhoto),
[video en iOS](https://webkit.org/blog/6784/new-video-policies-for-ios/).

## Limites para ID-01C

No se mide desenfoque, reflejos, legibilidad OCR, identidad, liveness o fraude.
No hay correccion de perspectiva ni recorte automatico. Distancia minima de
enfoque, resolucion nativa, memoria y orientacion dependen del dispositivo.
El usuario revisa nitidez/completitud antes de aceptar; no es un quality gate.
