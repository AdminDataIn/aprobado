param(
    [Parameter(Mandatory)][string]$Copia,
    [Parameter(Mandatory)][string]$Salida,
    [ValidateRange(5,65536)][int]$UltimaFila = 65536
)

$ErrorActionPreference = 'Stop'
$file = (Resolve-Path -LiteralPath $Copia).Path
$dir = Split-Path -Parent $file
$target = [IO.Path]::GetFullPath($Salida)
if ([IO.Path]::GetExtension($file) -ine '.xls' -or (Split-Path -Parent $target) -ne $dir -or
    [IO.Path]::GetExtension($target) -ine '.json' -or (Test-Path -LiteralPath $target) -or
    !(Get-Acl -LiteralPath (Split-Path -Parent $dir)).AreAccessRulesProtected) {
    throw 'Use una copia XLS y un JSON nuevo en la misma carpeta bajo custodia protegida.'
}

function Columna([int]$n) {
    $s = ''
    while ($n -gt 0) {
        $n -= 1
        $s = [char](65 + ($n % 26)) + $s
        $n = [Math]::Floor($n / 26)
    }
    $s
}

function Leer-Nota($cell) {
    if ($null -ne $cell.Comment) { [string]$cell.Comment.Text() }
}

function Leer-Regla($cell) {
    $v = $cell.Validation
    $type = [int]$v.Type
    $intervalo = if ($type -in 1,2,4,5,6,7) { [int]$v.Operator } else { $null }
    $segundo = if ($type -in 1,2,4,5,6 -and $intervalo -in 1,2) { [string]$v.Formula2 } else { $null }
    [ordered]@{
        tipo_excel = $type; operador_excel = $intervalo; formula1 = [string]$v.Formula1; formula2 = $segundo
        ignora_vacios = [bool]$v.IgnoreBlank; alerta = [bool]$v.ShowError
        estilo_alerta_excel = [int]$v.AlertStyle; titulo_error = [string]$v.ErrorTitle
        mensaje_error = [string]$v.ErrorMessage; titulo_entrada = [string]$v.InputTitle
        mensaje_entrada = [string]$v.InputMessage
    }
}

function Obtener-Especial($range, [int]$tipo) {
    try { return ,($range.SpecialCells($tipo)) } catch {
        if ($_.Exception.HResult -ne -2146827284 -and
            $_.Exception.InnerException.HResult -ne -2146827284) { throw }
    }
}

$before = @(Get-Process -Name EXCEL -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
Add-Type 'using System.Runtime.InteropServices; public static class FacilidatWindowPid { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(System.IntPtr h, out uint id); }'
$app = $null
$view = $null
$own = $false
$stage = 'instancia COM'
$initial = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
try {
    $app = New-Object -ComObject Excel.Application
    $pidExcel = [uint32]0
    [void][FacilidatWindowPid]::GetWindowThreadProcessId([IntPtr][long]$app.Hwnd, [ref]$pidExcel)
    if (!$pidExcel -or $pidExcel -in $before -or $app.Workbooks.Count -ne 0 -or
        $app.ProtectedViewWindows.Count -ne 0) { throw 'No es instancia exclusiva.' }
    $own = $true
    $app.AutomationSecurity = 3
    $app.EnableEvents = $false
    $stage = 'Vista protegida'
    $view = $app.ProtectedViewWindows.Open($file, '', $false, $false)
    $book = $view.Workbook
    $stage = 'metadatos'
    $out = [ordered]@{
        sha256 = $initial.ToLowerInvariant(); excel = $app.Version; modo = 'VISTA_PROTEGIDA'
        formato_excel = $book.FileFormat; vba_presente = $book.HasVBProject
        hojas_xlm = $book.Excel4MacroSheets.Count; hojas_xlm_internacionales = $book.Excel4IntlMacroSheets.Count
        conexiones = $book.Connections.Count
        vinculos_excel = @($book.LinkSources(1) | Where-Object {$_}).Count
        vinculos_ole = @($book.LinkSources(2) | Where-Object {$_}).Count
        fila_encabezados = 4; primera_fila_datos = 5; ultima_fila_inspeccionada = $UltimaFila
        hojas = @(); nombres = @(); campos = @(); celdas_catalogos = @(); controles = @()
    }
    $stage = 'rangos nombrados'
    $namesCount = [int]$book.Names.Count
    Write-Host "EXCEL_COM=ABIERTO | NOMBRES=$namesCount"
    for ($i=1; $i -le $namesCount; $i++) {
        $n = $book.Names.Item($i)
        $out.nombres += [ordered]@{nombre=[string]$n.Name; referencia=[string]$n.RefersTo; visible=[bool]$n.Visible; macro_tipo=[int]$n.MacroType}
    }
    for ($i=1; $i -le $book.Worksheets.Count; $i++) {
        $sheet = $book.Worksheets.Item($i)
        $stage = "hoja $i"
        $used = $sheet.UsedRange
        $out.hojas += [ordered]@{
            nombre=$sheet.Name; visibilidad=$sheet.Visible; rango=$used.Address()
            proteccion_celdas=$sheet.ProtectContents
        }
        if ($sheet.Name -eq 'Hoja1') {
            $stage = 'encabezados y validaciones'
            $last = [int]($used.Column + $used.Columns.Count - 1)
            $headers = $sheet.Range('A4', "$(Columna $last)4").Value2
            $types = $sheet.Range('A5', "$(Columna $last)5").Value2
            for ($j=1; $j -le $last; $j++) {
                if ([string]::IsNullOrWhiteSpace([string]$headers[1,$j])) { continue }
                $letter = Columna $j
                $header = $sheet.Cells.Item(4,$j)
                $first = $sheet.Cells.Item(5,$j)
                $domain = $sheet.Range("$($letter)5", "$($letter)$UltimaFila")
                $rules = @()
                $formulaAreas = @()
                $validations = if ($UltimaFila -gt 5) { Obtener-Especial $domain -4174 }
                if ($validations) {
                    $areaCount = [int]$validations.Areas.Count
                    if ($areaCount -gt 4096) { throw 'Exceso de regiones discontinuas de validacion.' }
                    for ($k=1; $k -le $areaCount; $k++) {
                        $area = $validations.Areas.Item($k)
                        $rules += [ordered]@{rango=$area.Address(); regla=(Leer-Regla ($area.Cells.Item(1,1)))}
                    }
                }
                if ($UltimaFila -eq 5) {
                    try { $rule = Leer-Regla $first } catch { $rule = $null }
                    if ($rule) { $rules += [ordered]@{rango="$($letter)5"; regla=$rule} }
                }
                if ($UltimaFila -eq 5) {
                    if ($first.HasFormula) { $formulaAreas += [ordered]@{rango="$($letter)5"; primera_formula=[string]$first.Formula} }
                } else {
                    $formulas = Obtener-Especial $domain -4123
                    if ($formulas) {
                        $areaCount = [int]$formulas.Areas.Count
                        if ($areaCount -gt 4096) { throw 'Exceso de regiones discontinuas de formulas.' }
                        for ($k=1; $k -le $areaCount; $k++) {
                            $area = $formulas.Areas.Item($k)
                            $formulaAreas += [ordered]@{rango=$area.Address(); primera_formula=[string]$area.Cells.Item(1,1).Formula}
                        }
                    }
                }
                $out.campos += [ordered]@{
                    columna=$letter; encabezado=[string]$headers[1,$j]; comentario=(Leer-Nota $header)
                    tipo_en_primera_fila=if ($null -eq $types[1,$j]) {'VACIA'} else {$types[1,$j].GetType().Name}
                    formato_excel=[string]$first.NumberFormat; formato_local=[string]$first.NumberFormatLocal
                    bloqueada=[bool]$first.Locked; validaciones=$rules; formulas=$formulaAreas
                }
            }
            Write-Host ('EXCEL_COM=CAMPOS_LEIDOS | CAMPOS='+$out.campos.Count)
            $stage = 'controles sin ejecucion'
            $nShapes = [int]$sheet.Shapes.Count
            for ($k=1; $k -le $nShapes; $k++) {
                $shape = $sheet.Shapes.Item($k)
                try { $ancla = $shape.TopLeftCell.Address() } catch { $ancla = $null }
                $out.controles += [ordered]@{nombre=$shape.Name; tipo_excel=$shape.Type; ancla=$ancla}
            }
        } elseif ($sheet.Name -eq 'Hoja2') {
            $stage = 'catálogos'
            if ($used.Count -gt 10000) { throw 'Área de catálogos excede el límite de revisión.' }
            $values = $used.Value2
            $f = $used.Formula
            $nRows = [int]$used.Rows.Count
            $nCols = [int]$used.Columns.Count
            $startRow = [int]$used.Row
            $startCol = [int]$used.Column
            for ($r=1; $r -le $nRows; $r++) {
                for ($j=1; $j -le $nCols; $j++) {
                    if ($null -eq $values[$r,$j] -and !$f[$r,$j]) { continue }
                    $formula = if ([string]$f[$r,$j] -like '=*') { [string]$f[$r,$j] } else { $null }
                    $out.celdas_catalogos += [ordered]@{
                        celda="$(Columna ($startCol+$j-1))$($startRow+$r-1)"; valor=$values[$r,$j]; formula=$formula
                    }
                }
            }
            $notes = @()
            for ($k=1; $k -le $sheet.Comments.Count; $k++) {
                $note = $sheet.Comments.Item($k)
                $notes += [ordered]@{celda=$note.Parent.Address(); nota=[string]$note.Text()}
            }
            $out.notas_catalogos = $notes
        }
    }
    $stage = 'integridad y escritura local'
    if ($out.campos.Count -lt 9 -or $initial -ne (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash) {
        throw 'Inventario incompleto o copia modificada.'
    }
    $json = $out | ConvertTo-Json -Depth 18
    $stream = [IO.File]::Open($target, [IO.FileMode]::CreateNew)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
        $stream.Write($bytes,0,$bytes.Length)
    } finally { $stream.Dispose() }
    [ordered]@{
        resultado='PASS'; hojas=@($out.hojas.nombre); campos=$out.campos.Count
        comentarios=@($out.campos | Where-Object comentario).Count
        nombres=$out.nombres.Count; xlm=($out.hojas_xlm+$out.hojas_xlm_internacionales)
    } | ConvertTo-Json -Compress
} catch {
    [Console]::Error.WriteLine(('EXCEL_COM=BLOCKED | etapa={0} linea={1} HRESULT=0x{2:X8} {3}' -f $stage,$_.InvocationInfo.ScriptLineNumber,$_.Exception.HResult,$_.Exception.GetType().Name))
    exit 1
} finally {
    if ($own) {
        if ($view) { try { $view.Close() } catch {} }
        try { $app.Quit() } catch {}
    }
    if ($app) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app) }
}
