# ID-01C / ID-01D: captura guiada y calidad local

## Alcance

Una implementacion compartida por Libranza, Prestadores y subsanacion que usa
la misma continuacion documental. No cambian endpoints, CaptureGrant, cookies,
CSRF, TTL, permisos, almacenamiento privado ni modelos. No hay migraciones.

La guia anterior a cada toma es una ilustracion CSS sin datos personales:
frontal con bloque de retrato y lineas; posterior con lineas y trama ilustrativa.
Portrait es la experiencia principal. Landscape oculta los controles y pide
girar; pausa el stream y lo recupera al volver a portrait. No es una restriccion
de autorizacion. Estados de finalizacion centrados con viewport y safe areas.

El visor usa contain y el marco se calcula dentro del video visible real.
La foto y el preview siguen sin recorte. Se conserva la captura nativa mediante
ImageCapture cuando existe y el fallback de dimensiones intrinsecas del video.
Zoom: capability real, maximo UX 2.5x o maximo del dispositivo si es menor,
pasos reales, debounce de 150 ms y valor aplicado de getSettings(). Se oculta
si no existe un rango util; un fallo/timeout no impide tomar una foto.
Focus continuo sigue siendo una mejora opcional segun capabilities.

## Flujo y contrato del analizador

`static/js/captura_quality.js` expone `DocumentCaptureQuality.analyze(blob,
{expected, aspect})`. Retorna `decision`, `reasons`, `message`, indicadores
internos opcionales `checks` y `elapsedMs`. No depende de un proveedor.

1. Tomar foto, detener tracks y mostrar preview con estado de revision.
2. Decodificar una miniatura de hasta 640 px en su lado mayor, sin modificar
   el archivo original que sera enviado. Procesar una vez por toma, no por frame.
3. Calcular luminancia, bordes Canny y componentes conectados.
4. Comprobar extremos de exposicion, contorno rectangular, contenido interior,
   nitidez, ocupacion y posicion dentro del marco.
5. `pass`: permitir usar la foto. `reject`: deshabilitar el envio, mostrar el
   problema y ofrecer repetir. El handler tambien comprueba esta decision.
6. `review`: SOLO ante fallo tecnico (dependencia, decodificacion o timeout),
   aviso visible y checkbox obligatorio de revision manual antes de enviar.
   No es una excepcion manual disponible para imagenes rechazadas por calidad.

Las URLs blob se liberan y las miniaturas se descartan. No se guardan metricas
en backend/storage/logs. No hay llamadas externas ni envio de fotos a terceros.
Timeout de decodificacion: 4 s; espera total de UI: 5 s. El procesamiento acotado
es sincrono; el timeout NO puede interrumpir un bloqueo del hilo principal.
Se cede un turno al navegador antes del calculo. No se anadio Worker sin evidencia
de necesidad; revisar tiempos en dispositivos lentos antes de ampliar el analisis.

## Umbrales centralizados

Todos estan en `DocumentCaptureQuality.config`, congelado en memoria:

| Control | Valor inicial / interpretacion |
| --- | --- |
| Resolucion de analisis | Lado mayor 640 px |
| Oscuridad | >=78% de pixeles con luminancia <28/255 |
| Sobreexposicion | >=82% de pixeles con luminancia >248/255 |
| Bordes | Gaussian 3; Canny 24/65 |
| Contraste interior | Desviacion estandar >=9 |
| Nitidez | Varianza Laplaciana >=24, luminancia 8 bits a escala de analisis |
| Contorno candidato | >=55 pixeles conectados, area >=1.5% de imagen |
| Proporcion horizontal | 1.15 a 2.15; guia ID-1 aproximadamente 1.586 |
| Cuatro lados | >=58% de 12 tramos por lado, banda de borde del 10% |
| Textura interior | Densidad de bordes entre 0.8% y 32%; >=3 de 8 zonas con textura |
| Interior evaluado | Excluir 12% de margen en cada lado del candidato |
| Ocupacion | >=30% del area del marco; no exige llenarlo exactamente |
| Encuadre | Centro dentro del 16% del ancho/alto del marco; 1.5% de margen de imagen |

Laplaciano: suma de los cuatro vecinos menos cuatro veces el pixel central;
se usa su varianza. La exposicion se comprueba en el marco y en el interior del
candidato. Se exige evidencia en los cuatro lados del componente, no solo una
linea de texto. Se evalua el candidato de mayor area. Una imagen nativa con
aspecto distinto al video (>10%) usa una guia centrada equivalente, sin asumir
que ambos sensores tienen exactamente el mismo campo de vision.

## Limites y calibracion pendiente

- Son heuristicas iniciales verificadas con escenas sinteticas, NO calibracion
  estadistica sobre cedulas reales. No afirmar sensibilidad/especificidad.
- Una pared, hoja vacia y teclado sinteticos se rechazan. Otros objetos con
  forma y textura similares pueden pasar: no se reconoce una cedula colombiana.
- Bajo contraste con la mesa, bordes fragmentados, inclinacion/perspectiva,
  reflejos o un objeto mayor en el fondo pueden producir falsos rechazos.
- El desenfoque se estima sobre una miniatura: texto diminuto o desenfoque
  localizado pueden escapar. Un archivo aceptado no garantiza legibilidad OCR.
- `glare_ok` permanece null. No hay detector separado de reflejos robustamente
  validado; se detecta solo sobreexposicion extrema, no reflejos localizados.
- Es un control UX en cliente, no una barrera antifraude ni una nueva autorizacion.
  Un cliente manipulado puede omitirlo; permanecen las validaciones del servidor.
- Los timeouts tecnicos permiten revision manual explicita, no aprobacion de
  identidad. OCR, extraccion, coherencia frontal/reverso, comparacion con solicitud,
  liveness y antifraude quedan fuera de esta entrega.

## Dependencia local verificable

JSFeat 0.0.8, licencia MIT conservada en
`static/js/vendor/jsfeat-0.0.8.LICENSE`. Distribucion original sin editar en
`static/js/vendor/jsfeat-0.0.8.min.js` (66093 bytes).

- Fuente: https://github.com/inspirit/jsfeat
- Primitivas: https://inspirit.github.io/jsfeat/
- Paquete: https://registry.npmjs.org/jsfeat/-/jsfeat-0.0.8.tgz
- SHA256 del JS: `49c74ca5820afd44c76abd1bf5c59e2e90fbef7fe2ca3ffa317935a83cd54b08`

Solo se invocan grayscale, Gaussian y Canny; no se cargan modelos/cascadas ni
se invocan rutinas biometricas. No hay CDN en runtime. El JS/CSS propio lleva
version `id01cd-1` en ambos templates para renovar cache sin cambiar seguridad.

## Validacion y UAT

Browser: `node --test gestion_creditos/tests/browser/test_captura_handoff.cjs`
(Playwright/Chromium). Escenas sinteticas sin PII incluyen nitida, blanca, luz
moderada, blur severo, negro, blanco saturado, pared, teclado, hoja vacia,
documento pequeno, cortado y descentrado. Se prueba que reject no hace POST,
review exige confirmacion, guias por cara, rotacion, zoom y flujo completo.

Integracion HTTPS real: `RUN_CAPTURE_GRANT_BROWSER=1` habilita
`gestion_creditos.tests.test_capture_grant_browser`; usa dos contextos de
navegador aislados, Django real, CSRF real y camara sintetica con analizador real.
La BD y storage son temporales. No se crean Credito/CreditoLibranza.

Validacion local de esta entrega: 70/70 pruebas browser; suite focal Django
381 pruebas (372 OK, 9 omitidas por PostgreSQL), incluida la integracion HTTPS.
`manage.py check`, `makemigrations --check --dry-run` y `git diff --check`: OK.

En Chromium de escritorio local, primeras mediciones por imagen 1080x1920:
aproximadamente 45-90 ms incluyendo decodificacion y reduccion. NO es una medida
de iPhone/Android. Las capturas de pantalla de pruebas se escriben en TEMP,
no en el repositorio. Revisar en dispositivos fisicos antes de produccion:

1. iPhone/Safari y Android/Chrome: ambas caras legibles a distancia comoda.
2. Luz interior/exterior, documentos de distintos formatos y fondos; registrar
   casos de rechazo injustificado sin guardar PII ni fotos sin consentimiento.
3. Zoom progresivo, autofocus, girar durante permiso/toma/revision y volver.
4. Preview sin recorte, controles visibles en pantalla pequena y safe areas.
5. Sin dependencia JSFeat: aviso/confirmacion manual; servicio sigue protegido.
6. PC detecta finalizacion; sin login movil, sin secretos en logs/storage.
7. Medir latencia en dispositivos lentos y recalibrar umbrales con evidencia.

Concurrencia PostgreSQL y UAT fisico no se sustituyen con SQLite ni emulacion.
