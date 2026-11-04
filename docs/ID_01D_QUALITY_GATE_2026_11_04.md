# ID-01D: correccion focal del quality gate

Fecha: 2026-11-04, America/Bogota. Paquete focal autorizado para publicacion
y despliegue controlado. La validacion Android fisica sigue pendiente.
Complementa `ID_01C_01D_CAPTURA_CALIDAD.md`; sus umbrales originales se conservan
salvo la segunda pasada de deteccion descrita aqui. No es verificacion de identidad.

## Causas reproducidas y correccion

1. Proporcion `w/h` exclusivamente horizontal: descartaba un documento completo
   girado 90 grados. Ahora usa lado mayor/lado menor, con los mismos limites
   1.15-2.15 y soporte de los cuatro lados. No rota ni modifica la foto.
2. Solo se validaba el rectangulo de mayor area: un fondo rectangular mayor
   podia ocultar un candidato valido. Ahora se recorren los candidatos hasta
   encontrar uno que cumpla TODOS los controles; cada intento tiene checks propios.
3. Canny 24/65 no encontraba el borde exterior de contraste bajo aunque habia
   contenido interior legible en la escena sintetica. Si ningun candidato primario
   pasa, se ejecuta una segunda deteccion Canny 6/16. No sustituye los bordes
   primarios usados para medir textura ni altera luminancia, contraste o nitidez.

Cada candidato recuperado sigue exigiendo componente >=55 pixeles, area >=1.5%,
proporcion valida, soporte >=7/12 tramos por lado, contraste interior >=9,
varianza Laplaciana >=24, textura 0.8%-32%, >=3/8 zonas, ocupacion >=30% del
marco, centrado <16% por eje y margen exterior >1.5%. Exposicion intacta.

No se identifica el tipo legal del documento, no se asegura lectura OCR y no
se marca identidad como verificada. Un objeto con forma y contenido similares
puede pasar esta heuristica visual; esa limitacion ya existia.

## Contrato e instrumentacion

`DocumentCaptureQuality.analyze(blob, {expected, aspect, diagnostics})` conserva
`decision`, `reasons`, `message`, `checks`, `elapsedMs`. Los rechazos mantienen
`reasons: ['missing']` y agregan `rejectionCode`:

- `missing_no_contour`: no se logro delimitar un candidato; NO prueba que este cortado.
- `missing_low_contrast`: candidato con contraste interior insuficiente.
- `missing_texture`: candidato sin detalle suficiente o con textura fuera de rango.

Los mensajes ahora orientan sobre fondo, luz o enfoque, sin atribuir el fallo
a manchas o antiguedad. Si todos fallan, se informa el primer fallo candidato
de la pasada primaria (o de recuperacion si no habia primarios).

Solo `diagnostics: true` booleano habilita metricas adicionales. La aplicacion
normal NO lo solicita. No se agregaron flags globales, parametros URL, envios,
console logs, almacenamiento o correlacion con solicitudes/identidades.
Incluye dimensiones decodificadas/analizadas, orientacion, conteos de candidatos,
pasada seleccionada, estadisticas interiores, ocupacion, offsets del centro,
subcodigo y uso del marco preview/fallback. No conserva blobs ni pixeles.

Esta opcion es una herramienta local de depuracion, no un control de permisos
del servidor. Solo utilizarla en una sesion expresamente autorizada y privada.
No exportar fotos ni vincular metricas a identificaciones.

Para observar una toma autorizada sin imprimir ni guardar la imagen, el tecnico
puede instalar temporalmente este wrapper en DevTools antes de tomar la foto:

```javascript
(() => {
  const original = window.DocumentCaptureQuality;
  const state = {last: null, stop() {
    window.DocumentCaptureQuality = original;
    this.last = null;
    delete window.ID01DDebug;
  }};
  window.ID01DDebug = state;
  window.DocumentCaptureQuality = Object.freeze({...original,
    analyze: async (photo, options = {}) => {
      const result = await original.analyze(photo, {...options, diagnostics: true});
      if (window.ID01DDebug === state) state.last = result.diagnostics || null;
      return result;
    },
  });
})();
```

Leer solo `ID01DDebug.last` localmente. No es telemetria ni habilita el envio
de una foto rechazada. Al terminar ejecutar `ID01DDebug.stop()` o recargar.
No se afirma borrado criptografico de memoria. No instalar este wrapper globalmente.

## Inventario candidato (7 archivos)

- `static/js/captura_quality.js`: detector, subcodigos y diagnostico opt-in.
- `templates/gestion_creditos/captura_continuacion.html`: version `id01d-2`
  del quality gate; los demas assets/flujo de captura no cambian.
- `gestion_creditos/tests/browser/document_fixture.cjs`: escenas sinteticas.
- `gestion_creditos/tests/browser/test_captura_quality.cjs`: matriz comparativa,
  privacidad, mapeo preview/nativo y EXIF. Nuevo.
- `gestion_creditos/tests/browser/test_captura_handoff.cjs`: nueve casos nuevos
  de Android emulado y captura nativa/fallback; conservar sus cambios previos ajenos.
- `gestion_creditos/tests/test_captura_documental.py`: expectativa de version
  del asset en la continuacion compartida Libranza/Prestadores.
- Este documento.

El test de handoff ya estaba modificado antes del ticket. Este candidato se
prepara en un worktree limpio desde 9916be1 y contiene solo los hunks ID01D,
sin el test pendiente de upload lento del checkout principal. No modelos,
migraciones, Python productivo, OCR, scoring, firma o reglas financieras.

## Comparacion sintetica

| Escena | Antes | Despues |
| --- | --- | --- |
| Normal, fondo claro/oscuro, luz moderada, desgaste simulado (5) | pass | pass |
| Bajo contraste exterior y detalle interior suficiente | missing | pass, recuperacion |
| Documento completo girado 90 grados | missing | pass, primaria |
| Documento valido y rectangulo mayor distractor | missing | pass, candidato alternativo |
| Cortes en cualquiera de los cuatro bordes | reject | reject |
| Desenfoque, exposicion extrema, pared, teclado, rectangulo vacio | reject | reject |
| Detalle insuficiente, exceso de textura, tamano pequeno, descentrado | reject | reject |
| Bajo contraste + vacio/blur; giro + vacio/blur/pequeno | reject | reject |

28 escenas: 8 positivas, 20 negativas. No nuevas aceptaciones en esos 20
controles negativos. NO es una estimacion estadistica de falsos positivos
con documentos reales ni garantiza rechazo de cualquier objeto no documental.

## Pruebas y repeticion

- Suite dedicada: 35/35 PASS, 0 fallos/omisiones (28 escenas +7 contratos).
- Flujo focal existente: 31/31 PASS, 0 fallos/omisiones, incluidos9 casos
  Android emulado, giro del visor, guias, bloqueo del boton y no POST en rechazo.
- Django: 1/1 PASS para la continuacion compartida Libranza/Prestadores y su
  version de asset. Base SQLite de tests en memoria, destruida al finalizar;
  transporte HTTP externo, email y publicacion Celery bloqueados por mocks.
- JPEG sintetico con EXIF6: dimensiones decodificadas1920x1080 y una sola
  rotacion aplicada por el navegador. No se parsea ni registra EXIF de personas.
- Native ImageCapture y fallback se simulan; ningun dispositivo real conectado.
- Sintaxis JS y diff --check: PASS. Check Django: 0 errores; W001 de migracion0018
  en SQLite en memoria vacia (no se inspecciono ni migro produccion).

Comandos browser (PowerShell; Playwright instalado solo en carpeta ignorada,
sin dependencia nueva de produccion):

```powershell
$env:NODE_PATH="$PWD/.local_stash/id01d-tools/node_modules"
$env:CAPTURE_BROWSER_CHANNEL='msedge'
$env:CAPTURE_QUALITY_BASELINE_REF='9916be10f405d7db90f495c966e4c6232b81b579'
node --test gestion_creditos/tests/browser/test_captura_quality.cjs
$env:DJANGO_SETTINGS_MODULE='id01d_browser_settings'
$env:PYTHONPATH="$PWD/.local_stash;$PWD"
node --test --test-name-pattern="quality gate|guias por cara|ID01D|captura intrinseca|visor dedicado|stream vertical|cache busting" gestion_creditos/tests/browser/test_captura_handoff.cjs
```

El baseline Git es opcional; sin esa variable se ejecuta la regresion funcional
sin depender de Git. El helper local de settings desactiva dotenv, usa SQLite
en memoria, broker memory, email locmem e integraciones apagadas; Django solo
renderiza templates, sin usar una base operativa.

## Android fisico y repeticion con la usuaria

ANDROID_E2E_FISICO = PENDIENTE. Edge/Chromium con UA Android y tactil no
certifica Chrome Android, su autofocus, sensor, EXIF, FOV o rendimiento real.
Canvas conserva la imagen completa y preview usa contain. Un aspecto similar
no demuestra FOV identico; si difiere >=10% sigue usandose marco fallback.

Tras revision y autorizacion del candidato, probar primero en entorno controlado:

1. Confirmar carga del asset `id01d-2` en Chrome Android actualizado.
2. Con consentimiento y custodia autorizados, documento completo, horizontal,
   centrado, fondo uniforme contrastante y luz difusa; no reutilizar WhatsApp
   como fixture ni fotografiar la pantalla del telefono como entrada al gate.
3. Tomar frente y revisar que el texto se lea en la foto. Si rechaza, recoger
   solo subcodigo y metricas locales autorizadas; no fotos/identidad en tickets.
4. Repetir giro portrait-landscape-portrait; comparar camara nativa y fallback
   en dispositivo de laboratorio sin forzar ni omitir el gate en la sesion real.
5. Comprobar que reject bloquea usar/enviar y que pass permite continuar al
   flujo documental existente, sin equivaler a identidad verificada.

## Riesgos residuales y gate

- Umbrales de recuperacion validados con geometria sintetica, no calibracion
  poblacional de cedulas antiguas; hacen falta pruebas fisicas autorizadas.
- Segunda pasada y mas candidatos aumentan trabajo local en casos rechazados;
  medir en Android de gama baja. El timeout no interrumpe el calculo sincrono.
- No hay homografia, reconocimiento semantico de cedula ni garantia OCR.
- Causa exacta de la foto del incidente sigue no acreditada retrospectivamente.
- Android fisico y repeticion real quedan pendientes, sin declarar el E2E
  desbloqueado por estas pruebas sinteticas.

## Liberacion controlada autorizada

Base productiva y remota verificada: 9916be10f405d7db90f495c966e4c6232b81b579.
Rama: release/prestadores-prod-2026-08-28. Sin cambios rastreados productivos;
se conservan los tres respaldos .env.bak_* existentes. No incluir ZapSign.

Staging identificado: staging-api enruta a fundetec_staging_backend; no se
acredito una instalacion representativa de captura documental ID01D alli.
Usar liberacion productiva controlada autorizada, no desplegar en el backend
educativo ni modificarlo.

Secuencia: backup protegido de git archive del SHA anterior y del asset
publicado, verificar recuperacion de bytes, push normal del SHA focal,
merge --ff-only exacto en VPS, check Django, publicacion atomica exclusivamente
de static/js/captura_quality.js en STATIC_ROOT/js/captura_quality.js y restart
unicamente de gunicorn.service para vaciar el cache de templates. Sin migrate,
collectstatic general, nginx reload, Celery, cambios de flags ni BD.

Verificar integridad del JS HTTPS servido con ?v=id01d-2, template renderizado
con contexto sintetico en memoria, healthchecks y browser smoke usando esos
assets/template con backend interceptado. No crear sesiones operativas para
pruebas tecnicas. Esta evidencia no reemplaza Android fisico ni envio real.

Rollback autorizado ante regresion critica: exigir HEAD exacto del hotfix y
tracked limpio, git revert --no-edit SHA_HOTFIX (sin reset/force), restaurar
atomicamente el asset respaldado y reiniciar solo Gunicorn. Validar salud y
la referencia anterior del template. Sin restaurar ni alterar PostgreSQL.
Conservar el backup y conciliar la rama revertida antes de otra publicacion.
