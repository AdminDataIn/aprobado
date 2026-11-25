param(
    [Parameter(Mandatory)][string]$CopiaBase,
    [Parameter(Mandatory)][string]$Diccionario,
    [Parameter(Mandatory)][string]$Salida
)

$ErrorActionPreference = 'Stop'
$base = (Resolve-Path -LiteralPath $CopiaBase).Path
$structure = (Resolve-Path -LiteralPath $Diccionario).Path
$target = [IO.Path]::GetFullPath($Salida)
$dir = Split-Path -Parent $base
if ((Split-Path -Parent $structure) -ne $dir -or (Split-Path -Parent $target) -ne $dir -or
    !([IO.Path]::GetFileName($target) -match '^[a-zA-Z0-9_]*SINTETICA_NO_REMITIR\.json$') -or
    (Test-Path -LiteralPath $target) -or !(Get-Acl -LiteralPath (Split-Path -Parent $dir)).AreAccessRulesProtected) {
    throw 'El destino debe ser un nuevo archivo SINTETICA_NO_REMITIR.json en custodia.'
}
$schema = Get-Content -Raw -LiteralPath $structure | ConvertFrom-Json
$sha = (Get-FileHash -LiteralPath $base -Algorithm SHA256).Hash
if ($schema.modo -ne 'VISTA_PROTEGIDA' -or $schema.sha256 -ine $sha -or $schema.formato_excel -ne 56 -or
    $schema.hojas_xlm -ne 0 -or $schema.hojas_xlm_internacionales -ne 0 -or
    $schema.vinculos_excel -ne 0 -or $schema.vinculos_ole -ne 0 -or $schema.conexiones -ne 0 -or
    $schema.primera_fila_datos -ne 5 -or $schema.ultima_fila_inspeccionada -lt 7 -or
    @($schema.campos).Count -ne 36 -or @($schema.nombres | Where-Object macro_tipo -NE -4142).Count -gt 0) {
    throw 'No se acreditaron estructura, ausencia de XLM y de vinculos externos en Vista protegida.'
}

$office = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration'
if ([string]$office.ProductReleaseIds -notmatch 'O365' -or [version]$office.VersionToReport -lt [version]'16.0.18730.20000') {
    throw 'No se acredita Microsoft 365 con ActiveX bloqueado de forma predeterminada.'
}
foreach ($key in @(
    'HKCU:\Software\Microsoft\Office\Common\Security', 'HKLM:\Software\Microsoft\Office\Common\Security',
    'HKCU:\Software\Microsoft\Office\16.0\Common\Security', 'HKLM:\Software\Microsoft\Office\16.0\Common\Security',
    'HKCU:\Software\Policies\Microsoft\Office\Common\Security', 'HKLM:\Software\Policies\Microsoft\Office\Common\Security',
    'HKCU:\Software\Policies\Microsoft\Office\16.0\Common\Security', 'HKLM:\Software\Policies\Microsoft\Office\16.0\Common\Security',
    'HKCU:\Software\Policies\Microsoft\Cloud\Office\16.0\Common\Security'
)) {
    $config = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
    if ($config -and $null -ne $config.DisableAllActiveX -and $config.DisableAllActiveX -ne 1) {
        throw 'Existe una excepcion a la restriccion de ActiveX. No se cambia la politica.'
    }
}

# Fixture cases are local structural tests, not valid identities or a report for transmission.
$catalogo = @{}
foreach ($cell in $schema.celdas_catalogos) { $catalogo[$cell.celda] = $cell.valor }
if ($catalogo['B12'] -ne 'AAAAMMDD' -or $catalogo['B21'] -ne '3') {
    throw 'Configuracion de fecha o entrega distinta de la leida.'
}
$corte = [datetime]::ParseExact([string]$catalogo['B26'],'dd/MM/yyyy',[Globalization.CultureInfo]::InvariantCulture)
$f = $corte.AddMonths(-6).ToString('yyyyMMdd')
$g = $corte.AddMonths(6).ToString('yyyyMMdd')
$n = $corte.ToString('yyyyMMdd')
$prev = $corte.AddDays(-30).ToString('yyyyMMdd')
$cases = @(
    @{Id='ZTEST0001'; Num='SINTETICA01'; I='0'; J='01'; M='1'; R='000'; T='500'; W='0'; Y='7'; Z='0'; AC=$corte.AddMonths(1).ToString('yyyyMMdd'); AD=$n},
    @{Id='ZTEST0002'; Num='SINTETICA02'; I='0'; J='06'; M='2'; R='030'; T='700'; W='100'; Y='5'; Z='1'; AC=$prev; AD=$corte.AddDays(-60).ToString('yyyyMMdd')},
    @{Id='ZTEST0003'; Num='SINTETICA03'; I='1'; J='05'; M='3'; R='000'; T='0'; W='0'; Y='12'; Z='0'; AC=$n; AD=$n}
)
$data = @(
    foreach ($x in $cases) {
        ,@(
            '5',$x.Id,$x.Num,'PERSONA SINTETICA NO REMITIR','0',$f,$g,'00',$x.I,$x.J,'0',$f,$x.M,$n,'00','00000000','A',$x.R,
            '1200',$x.T,'0','100',$x.W,'12',$x.Y,$x.Z,'0','00000000',$x.AC,$x.AD,'BOGOTA','11001','BOGOTA DC',
            'DIRECCION SINTETICA','prueba@example.invalid','0000000000'
        )
    }
)

$before = @(Get-Process -Name EXCEL -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
Add-Type 'using System.Runtime.InteropServices; public static class FacilidatSyntheticPid { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(System.IntPtr h, out uint id); }'
$app = $null
$book = $null
$own = $false
$stage = 'abrir copia protegida'
try {
    $stage = 'instancia COM'
    $app = New-Object -ComObject Excel.Application
    $pidExcel = [uint32]0
    [void][FacilidatSyntheticPid]::GetWindowThreadProcessId([IntPtr][long]$app.Hwnd, [ref]$pidExcel)
    if (!$pidExcel -or $pidExcel -in $before -or $app.Workbooks.Count -ne 0 -or
        $app.ProtectedViewWindows.Count -ne 0) { throw 'No es instancia exclusiva.' }
    $own = $true
    $app.AutomationSecurity = 3
    $app.EnableEvents = $false
    $app.AskToUpdateLinks = $false
    $stage = 'abrir copia sin vinculos'
    $book = $app.Workbooks.Open($base, 0, $true)
    if (!$book.ReadOnly -or $book.FileFormat -ne 56) { throw 'No se acredita lectura exclusiva en XLS nativo.' }
    $sheet = $book.Worksheets.Item('Hoja1')
    $app.Calculation = -4135
    $stage = 'verificar celdas de prueba'
    $checked = 0
    for ($i = 0; $i -lt 3; $i++) {
        for ($j = 0; $j -lt 36; $j++) {
            $cell = $sheet.Cells.Item(5+$i, 1+$j)
            if ($cell.Locked -or $cell.HasFormula -or $null -ne $cell.Value2 -or $cell.NumberFormat -ne '@') {
                throw 'La celda no es un destino vacio de texto sin formula.'
            }
            $rules = @($schema.campos[$j].validaciones | Where-Object {$_.regla.tipo_excel -eq 3})
            if ($rules.Count) {
                $allowed = @($rules[0].regla.formula1 -split ';' | ForEach-Object {$_.Trim()})
                if ($data[$i][$j] -cnotin $allowed) { throw 'Dato de prueba fuera del catalogo.' }
                $checked++
            }
        }
    }
    $stage = 'escribir A5:AJ7'
    $values = New-Object 'object[,]' 3,36
    for ($i = 0; $i -lt 3; $i++) {
        for ($j = 0; $j -lt 36; $j++) { $values[$i,$j] = [string]$data[$i][$j] }
    }
    $region = $sheet.Range('A5:AJ7')
    $region.Value2 = $values
    $got = $region.Value2
    for ($i = 1; $i -le 3; $i++) {
        for ($j = 1; $j -le 36; $j++) {
            if ([string]$got[$i,$j] -cne [string]$values[($i-1),($j-1)]) {
                throw 'El texto guardado no coincide con el fixture.'
            }
        }
    }
    # With ActiveX blocked, saving a BIFF8 copy in this Excel loses controls.
    # Only exercise the destination cells in memory; never save the workbook.
    $stage = 'cerrar copia sin guardar'
    $book.Close($false)
    $book = $null
    if ((Get-FileHash -LiteralPath $base -Algorithm SHA256).Hash -ine $sha) { throw 'Cambio en la referencia.' }
    $stage = 'registrar fixture local'
    $fixture = [ordered]@{
        tipo='PRUEBA_SINTETICA_NO_REMITIR'; sha256_plantilla=$sha.ToLowerInvariant(); destino_excel='Hoja1!A5:AJ7'
        columnas=@($schema.campos.columna); encabezados=@($schema.campos.encabezado); datos=$data
        celdas_cotejadas=108; celdas_catalogo_cotejadas=$checked; xls_guardado=$false
    }
    $payload = $fixture | ConvertTo-Json -Depth 10
    $stream = [IO.File]::Open($target,[IO.FileMode]::CreateNew)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($payload)
        $stream.Write($bytes,0,$bytes.Length)
    } finally { $stream.Dispose() }
    [ordered]@{resultado='PASS';filas=3;campos=36;celdas_catalogo_cotejadas=$checked;guardado_excel='NO_EJECUTADO';transmision='NO_EJECUTADA'} | ConvertTo-Json -Compress
} catch {
    [Console]::Error.WriteLine(('XLS_SINTETICO=BLOCKED | etapa={0} linea={1} HRESULT=0x{2:X8}' -f $stage,$_.InvocationInfo.ScriptLineNumber,$_.Exception.HResult))
    exit 1
} finally {
    if ($own) {
        if ($book) { try { $book.Close($false) } catch {} }
        try { $app.Quit() } catch {}
    }
    if ($app) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app) }
}
