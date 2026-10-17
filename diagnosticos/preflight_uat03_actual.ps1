param(
    [ValidateSet('PREPARACION', 'OPERATIVO')]
    [string]$Modo = 'PREPARACION',
    [string]$ReleaseSha = $env:APROBADO_UAT_RELEASE_SHA,
    [int]$SolicitudId = 0,
    [int]$PropietarioId = 0,
    [int]$ActorId = 0
)

$ErrorActionPreference = 'Stop'
$base = '4c536c148e946e13aefdc9c0012fdeccdb048123'
function Bloquear([string]$Control) {
    @{ resultado = 'BLOCKED_NO_DESPLEGADO'; control = $Control; autorizacion_consumo = $false } |
        ConvertTo-Json -Compress
    exit 2
}
if ($ReleaseSha -cnotmatch '^[0-9a-f]{40}$') { Bloquear 'release_sha40_explicito_requerido' }
if ($ReleaseSha -ceq $base) { Bloquear 'release_base_sin_ingreso' }

if ($Modo -eq 'PREPARACION') {
    $raiz = Split-Path -Parent $PSScriptRoot
    foreach ($ref in @('HEAD', 'refs/remotes/origin/release/prestadores-prod-2026-08-28')) {
        $sha = & git -C $raiz rev-parse --verify "$ref^{commit}" 2>$null
        if ($LASTEXITCODE -ne 0 -or $sha -cne $ReleaseSha) { Bloquear 'release_local_no_coincide' }
    }
    & git -C $raiz merge-base --is-ancestor $base HEAD
    if ($LASTEXITCODE -ne 0) { Bloquear 'release_no_desciende_de_base' }
    & git -C $raiz diff --quiet HEAD --
    if ($LASTEXITCODE -ne 0) { Bloquear 'codigo_rastreado_modificado' }
    # Local preparation never attests deployment, schema, policy or consent.
    Bloquear 'preparacion_no_acredita_despliegue'
}

if ($SolicitudId -le 0 -or $SolicitudId -in @(3, 4, 9) -or $PropietarioId -le 0 -or $ActorId -le 0) {
    Bloquear 'ids_operativos_explicitos_requeridos'
}
$codigo = @'
import importlib.util, json, logging, os, runpy, sys
from pathlib import Path
sys.dont_write_bytecode = True
logging.disable(logging.CRITICAL)
try:
    os.chdir('/root/aprobado_web')
    modulo = 'diagnosticos.aprobado_uat03_controlada'
    spec = importlib.util.find_spec(modulo)
    if spec is None or Path(spec.origin).resolve() != Path('/root/aprobado_web/diagnosticos/aprobado_uat03_controlada.py'):
        raise RuntimeError('runner_no_instalado')
    os.environ.update(APROBADO_UAT_MODO='preflight', APROBADO_UAT_RELEASE_SHA='__RELEASE__',
                      APROBADO_UAT_SOLICITUD_ID='__SOLICITUD__', APROBADO_UAT_PROPIETARIO_ID='__PROPIETARIO__',
                      APROBADO_UAT_ACTOR_ID='__ACTOR__', APROBADO_UAT_HELPERS_DIR='/tmp')
    runpy.run_module(modulo, run_name='__main__')
except Exception:
    print(json.dumps({'resultado':'BLOCKED_NO_DESPLEGADO', 'control':'runner_no_instalado_o_incompatible',
                      'autorizacion_consumo':False}))
    raise SystemExit(2) from None
'@
$codigo = $codigo.Replace('__RELEASE__', $ReleaseSha).Replace('__SOLICITUD__', [string]$SolicitudId).
    Replace('__PROPIETARIO__', [string]$PropietarioId).Replace('__ACTOR__', [string]$ActorId)
$codigo | ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 aprobado-vps "PYTHONDONTWRITEBYTECODE=1 /root/aprobado_web/venv/bin/python /root/aprobado_web/manage.py shell"
if ($LASTEXITCODE -ne 0) { throw 'Preflight bloqueado; no ejecutar consumo' }
