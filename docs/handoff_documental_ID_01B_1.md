# ID-01B.1: CaptureGrant cross-device

## Alcance

El PC sigue autenticado. El movil canjea un QR y NO inicia sesion Django ni
recibe credenciales del PC. No se modifica visor, getUserMedia, canvas, calidad,
procesamiento/limites de imagen, storage privado ni originacion financiera.
La camara tipo ZapSign se reserva a ID-01B.2.

## Credenciales y persistencia

0048_captura_documental_capture_grant depende de 0047 y agrega solamente:

- capture_grant_hash: CharField(64), blank=True, editable=False.
- capture_grant_expira_en: DateTimeField nullable, editable=False.
- capture_grant_revocado_en: DateTimeField nullable, editable=False.

El token QR y el grant son secretos independientes de secrets.token_urlsafe(32).
Solo persisten hashes SHA-256. El canje valida UUID/producto/contexto/propietario
vigente/token/estado/TTL bajo transaction.atomic y select_for_update(of=self).
El token se consume, estado pasa de ABIERTA a CANJEADA y se emite un grant
limitado a expira_en original (por defecto 600 s desde crear, no desde canjear).
vinculo_hash queda reservado al flujo autenticado anterior, no al grant.

Una sola funcion _autorizar_grant autoriza operaciones delegadas bajo lock.
Los wrappers del propietario y del grant comparten _recibir_captura y
_finalizar_sesion. Nunca se reemplaza request.user por el propietario.
Captura.actor conserva la cuenta responsable; metadata.canal=CAPTURE_GRANT y
eventos CANJE_DELEGADO/FRONTAL_RECIBIDO_DELEGADO/TRASERA_RECIBIDO_DELEGADO/
FINALIZACION_DELEGADA usan actor=NULL: no afirman un login del movil.

## HTTP y cookie

Base: /captura-documental/<producto>/<uuid>/movil/

| Ruta | Metodo | Credencial |
| --- | --- | --- |
| base | GET | Ninguna. Shell neutro con CSRF; sin consultar existencia de la sesion |
| canjear/ | POST | Token QR + CSRF |
| estado/ | GET | CaptureGrant |
| frontal/ | POST | CaptureGrant + CSRF |
| trasera/ | POST | CaptureGrant + CSRF |
| finalizar/ | POST | CaptureGrant + CSRF |

Cookie: __Secure-capture_grant_token; HttpOnly; Secure; SameSite=Strict;
sin Domain; Path=base de ESA sesion; Max-Age=floor(TTL restante), nunca renovado
por polling. No se emite sessionid. El grant no permite descargas ni endpoints
de usuario/credito/perfil, regenerar/revocar o actuar financieramente.

La URL lleva #token; no se envia en GET. El JS lo conserva solo en memoria y
en la barra hasta que el canje responde exitosamente; entonces elimina el
fragmento. Al recargar sin fragmento usa la cookie. Sin grant valido muestra
captura no disponible, no exige login general ni expone motivos internos.

Regeneracion/revocacion borran el hash y registran revocacion. Finalizar invalida
el grant y expira su cookie en la respuesta. Tras vencimiento deja de autorizar;
una consulta con el grant vencido o la purga registra invalidacion persistida.
El PC detecta FINALIZADA usando su propio endpoint autenticado.

## CSRF y privacidad

Shell con ensure_csrf_cookie y token en template. POST usa X-CSRFToken y cookies
same-origin. Referrer-Policy y fetch referrerPolicy usan same-origin, permitiendo
la comprobacion HTTPS de Referer cuando falta Origin. No hay csrf_exempt.
No se incluyen fragmentos en Referer; no hay requests a proveedores QR externos.
Origin incorrecto, cookie/token CSRF ausentes o incorrectos siguen rechazados.

Errores de grant son 403 genericos; payload/imagen invalido, 400 generico. Se
limitan intentos de canje (20/600 s) y escritura (60/600 s) con el helper de cache
existente. Respuestas no-store/private. Variables/token y cookie con nombre
TOKEN se protegen del reporte Django de errores; no registrar cuerpos/cookies
ni capturas en instrumentacion externa.

## Validacion

Sobre base de pruebas, nunca produccion:

```bash
python manage.py test gestion_creditos.tests.test_capture_grant gestion_creditos.tests.test_captura_documental gestion_creditos.tests.test_documentos_privados contractors gestion_creditos.tests.test_originacion_libranza gestion_creditos.tests.test_integridad_originacion_anulacion --verbosity 2
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
```

PostgreSQL requerido para test_capture_grant_canje_unico en
gestion_creditos.tests.test_captura_documental.CapturaConcurrenciaPostgresTest.
SQLite omite explicitamente los tests de concurrencia.

Browser: mismas dependencias temporales de Node/Playwright/jsQR del documento
ID-01B. No se agregan dependencias runtime de la aplicacion. Ademas del modulo
browser con APIs simuladas, ejecutar:

```powershell
$env:NODE_PATH="$env:TEMP/aprobado-id01b-tools/node_modules"
$env:PLAYWRIGHT_BROWSERS_PATH="$env:TEMP/aprobado-id01b-browsers"
node --test gestion_creditos/tests/browser/test_captura_handoff.cjs
$env:RUN_CAPTURE_GRANT_BROWSER='1'
python manage.py test gestion_creditos.tests.test_capture_grant_browser --verbosity 2
```

El ultimo test usa DB aislada Django, servidor real y proxy TLS local con
certificado efimero (cryptography). Dos contextos Chromium independientes:
solo el PC recibe una sesion de test; el movil empieza sin cookies. Decodifica
el QR, canjea, prueba CSRF, sube JPEG de camara sintetica, recarga con grant,
finaliza, comprueba invalidacion y confirmacion desktop. No usa endpoints mock
ni documentos reales. El wizard PC se posiciona directamente en el paso que
contiene el handoff: no pretende validar todo el formulario de originacion.

## UAT en clon HTTPS

1. Confirmar base de clon antes de aplicar la migracion 0048 y publicar assets
   del bloque en el entorno de validacion. No ejecutar estos pasos en produccion.
2. PC autenticado (incluir cuenta Google): abrir solicitud Libranza, completar
   hasta documento y generar QR. Dejar esa pestana abierta.
3. iPhone/Safari sin login de Aprobado: escanear, abrir shell neutro y pulsar
   Abrir camara. No debe aparecer login. El fragmento desaparece tras el canje.
4. Aceptar frontal; recargar antes de posterior. Debe continuar sin login ni QR
   nuevo. Aceptar posterior y finalizar; desktop confirma FINALIZADA.
5. Confirmar en inspeccion que el movil no obtuvo sessionid; grant HttpOnly,
   Secure, Strict, path acotado y eliminado al finalizar. No compartir sus valores.
6. Repetir PRESTADORES con y sin solicitud previa, y Chrome/Android.
7. En otra captura, regenerar/revocar desde endpoint autenticado del propietario;
   el movil anterior no debe poder cargar/finalizar. Repetir expiracion y segundo
   canje del QR desde otro navegador: solo un canje gana.
8. Con solo grant, abrir rutas normales de cuenta/credito y descargar documentos
   debe seguir exigiendo sus permisos habituales. No continuar originacion real.

## Limites residuales

- Un QR robado antes del canje concede captura de esa sesion: tratarlo como secreto.
  Esto no acredita identidad ni prueba de vida.
- Safari/Android fisicos y concurrencia PostgreSQL requieren UAT especifico.
- Cambio entre navegador embebido y Safari no transporta cookies. Abrir el enlace
  en el navegador elegido ANTES del canje; si se pierde el grant, regenerar desde PC.
- Si se pierde la respuesta de finalizar, el grant ya no sirve: verificar el
  estado en PC; no se crea otra finalizacion ni se reactiva automaticamente.
- El rate limit existente usa cache y es fail-open/no atomico; necesita cache
  compartida y proxy confiable para eficacia multiproceso. No reemplaza token,
  locks, TTL, CSRF ni validacion del grant.
- La cookie Secure exige HTTPS para UAT cross-device; no desactivarla para una IP HTTP.
