param(
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][string]$DaxQuery,
    [string]$Database = "",
    [string]$Roles = "",
    [string]$EffectiveUserName = "",
    [string]$AdomdDir = "",
    [int]$CommandTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

function Resolve-AdomdDll {
    param([string]$Hint)
    $dllName = "Microsoft.AnalysisServices.AdomdClient.dll"
    $candidates = @()
    if ($Hint) { $candidates += (Join-Path $Hint $dllName) }
    if ($env:PBIR_ADOMD_DIR) { $candidates += (Join-Path $env:PBIR_ADOMD_DIR $dllName) }
    $candidates += (Join-Path $env:LOCALAPPDATA "Programs\DAX Studio\bin\$dllName")
    $candidates += "C:\Program Files\DAX Studio\bin\$dllName"

    foreach ($dll in $candidates) {
        if ($dll -and (Test-Path $dll)) {
            return $dll
        }
    }
    throw "AdomdClient DLL not found. Checked: $($candidates -join '; ')"
}

$conn = $null
$cmd = $null
$reader = $null

try {
    $dllPath = Resolve-AdomdDll -Hint $AdomdDir
    Add-Type -Path $dllPath

    $parts = @("Data Source=localhost:$Port")
    if ($Database) { $parts += "Initial Catalog=$Database" }
    if ($Roles) { $parts += "Roles=$Roles" }
    if ($EffectiveUserName) { $parts += "EffectiveUserName=$EffectiveUserName" }
    $connStr = ($parts -join ";") + ";"

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection
    $conn.ConnectionString = $connStr
    $conn.Open()

    $cmd = $conn.CreateCommand()
    $cmd.CommandTimeout = $CommandTimeoutSeconds
    $cmd.CommandText = $DaxQuery
    $reader = $cmd.ExecuteReader()

    $columns = @()
    for ($i = 0; $i -lt $reader.FieldCount; $i++) {
        $columns += [string]$reader.GetName($i)
    }

    $rows = @()
    while ($reader.Read()) {
        $rowObj = [ordered]@{}
        for ($i = 0; $i -lt $reader.FieldCount; $i++) {
            $colName = $columns[$i]
            $val = $reader.GetValue($i)
            if ($val -is [System.DBNull] -or $null -eq $val) {
                $rowObj[$colName] = $null
            }
            elseif ($val -is [System.DateTime]) {
                $rowObj[$colName] = $val.ToString("o")
            }
            else {
                $rowObj[$colName] = $val
            }
        }
        $rows += [PSCustomObject]$rowObj
    }
    $sw.Stop()

    [PSCustomObject]@{
        ok = $true
        duration_ms = [int]$sw.ElapsedMilliseconds
        columns = @($columns)
        rows = @($rows)
        row_count = $rows.Count
    } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    [PSCustomObject]@{
        ok = $false
        duration_ms = 0
        columns = @()
        rows = @()
        row_count = 0
        error = $_.Exception.Message
    } | ConvertTo-Json -Depth 4
    exit 1
}
finally {
    if ($null -ne $reader) {
        try { $reader.Close() } catch {}
        try { $reader.Dispose() } catch {}
    }
    if ($null -ne $cmd) {
        try { $cmd.Dispose() } catch {}
    }
    if ($null -ne $conn) {
        try { $conn.Close() } catch {}
        try { $conn.Dispose() } catch {}
    }
}
