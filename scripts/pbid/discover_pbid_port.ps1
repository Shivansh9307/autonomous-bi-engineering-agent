param(
    [string]$ReportName = "",
    [string]$AdomdDir = "",
    [string]$PbirBin = "pbir"
)

$ErrorActionPreference = "Stop"

function Resolve-TomDir {
    param([string]$Hint)
    $candidates = @()
    if ($Hint) { $candidates += $Hint }
    if ($env:PBIR_ADOMD_DIR) { $candidates += $env:PBIR_ADOMD_DIR }
    $candidates += (Join-Path $env:LOCALAPPDATA "Programs\DAX Studio\bin")
    $candidates += "C:\Program Files\DAX Studio\bin"

    foreach ($dir in $candidates) {
        if ($dir -and (Test-Path (Join-Path $dir "Microsoft.AnalysisServices.Tabular.dll"))) {
            return $dir
        }
    }
    throw "TOM assemblies not found. Checked: $($candidates -join '; ')"
}

function Get-MsmdsrvPortRecords {
    $procs = Get-Process -Name "msmdsrv" -ErrorAction SilentlyContinue
    if (-not $procs) {
        return @()
    }
    $pids = @($procs | ForEach-Object { [int]$_.Id })
    $records = @()

    try {
        $conns = Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { $pids -contains [int]$_.OwningProcess }
        foreach ($conn in $conns) {
            $records += [PSCustomObject]@{
                Port = [int]$conn.LocalPort
                MsmdsrvPid = [int]$conn.OwningProcess
            }
        }
    }
    catch {
        $netstatLines = netstat -ano | Select-String "LISTENING"
        foreach ($line in $netstatLines) {
            $tokens = ($line.ToString().Trim() -split "\s+")
            if ($tokens.Length -ge 5) {
                $procId = 0
                if ([int]::TryParse($tokens[-1], [ref]$procId) -and ($pids -contains $procId)) {
                    $portStr = ($tokens[1] -replace ".*:", "")
                    $portNum = 0
                    if ([int]::TryParse($portStr, [ref]$portNum)) {
                        $records += [PSCustomObject]@{
                            Port = $portNum
                            MsmdsrvPid = $procId
                        }
                    }
                }
            }
        }
    }

    $unique = @{}
    foreach ($rec in $records) {
        $key = "$($rec.Port):$($rec.MsmdsrvPid)"
        if (-not $unique.ContainsKey($key)) {
            $unique[$key] = $rec
        }
    }
    return @($unique.Values)
}

try {
    $tomDir = Resolve-TomDir -Hint $AdomdDir
    Add-Type -Path (Join-Path $tomDir "Microsoft.AnalysisServices.Core.dll")
    Add-Type -Path (Join-Path $tomDir "Microsoft.AnalysisServices.Tabular.dll")

    $desktopByPid = @{}
    try {
        $pbirRaw = & $PbirBin desktop list --json 2>$null
        if ($LASTEXITCODE -eq 0 -and $pbirRaw) {
            $parsed = $pbirRaw | ConvertFrom-Json
            $items = if ($parsed -is [System.Array]) { $parsed } elseif ($parsed.instances) { $parsed.instances } else { @() }
            foreach ($item in $items) {
                if ($null -ne $item.pid) {
                    $desktopByPid[[int]$item.pid] = $item
                }
            }
        }
    }
    catch {
        # pbir desktop list is optional enrichment
    }

    $portRecords = Get-MsmdsrvPortRecords
    $instances = @()

    foreach ($rec in $portRecords) {
        $parentPid = $null
        try {
            $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$($rec.MsmdsrvPid)" -ErrorAction SilentlyContinue
            if ($cim) {
                $parentPid = [int]$cim.ParentProcessId
            }
        }
        catch {
            $parentPid = $null
        }

        $desktopInfo = if ($null -ne $parentPid -and $desktopByPid.ContainsKey($parentPid)) { $desktopByPid[$parentPid] } else { $null }
        $openTarget = $null
        if ($desktopInfo) {
            if ($desktopInfo.PSObject.Properties["reportPath"]) { $openTarget = [string]$desktopInfo.reportPath }
            elseif ($desktopInfo.PSObject.Properties["path"]) { $openTarget = [string]$desktopInfo.path }
            elseif ($desktopInfo.PSObject.Properties["file"]) { $openTarget = [string]$desktopInfo.file }
            elseif ($desktopInfo.PSObject.Properties["name"]) { $openTarget = [string]$desktopInfo.name }
        }

        $server = New-Object Microsoft.AnalysisServices.Tabular.Server
        $dbName = $null
        $dbCount = 0
        try {
            $server.Connect("Data Source=localhost:$($rec.Port)")
            $dbCount = $server.Databases.Count
            if ($dbCount -eq 0) {
                continue
            }
            $dbName = [string]$server.Databases[0].Name
        }
        finally {
            if ($server.Connected) {
                $server.Disconnect()
            }
            $server.Dispose()
        }

        $instances += [PSCustomObject]@{
            port = $rec.Port
            msmdsrv_pid = $rec.MsmdsrvPid
            pbi_desktop_pid = $parentPid
            database = $dbName
            database_count = $dbCount
            open_target = $openTarget
        }
    }

    $selected = $null
    if ($instances.Count -gt 0) {
        if ($ReportName) {
            $needle = $ReportName.ToLowerInvariant()
            $selected = $instances | Where-Object {
                ($_.open_target -and $_.open_target.ToLowerInvariant().Contains($needle)) -or
                ($_.database -and $_.database.ToLowerInvariant().Contains($needle))
            } | Select-Object -First 1
        }
        if (-not $selected) {
            $selected = $instances | Select-Object -First 1
        }
    }

    $result = [PSCustomObject]@{
        ok = ($instances.Count -gt 0)
        instances = @($instances)
        selected = $selected
        error = if ($instances.Count -eq 0) { "No active Power BI Desktop Analysis Services model instances found." } else { $null }
    }
    $result | ConvertTo-Json -Depth 6
    if ($instances.Count -eq 0) { exit 1 }
    exit 0
}
catch {
    [PSCustomObject]@{
        ok = $false
        instances = @()
        selected = $null
        error = $_.Exception.Message
    } | ConvertTo-Json -Depth 4
    exit 1
}
