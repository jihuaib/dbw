#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'prod', 'down', 'start', 'stop', 'restart', 'status', 'build', 'logs')]
    [string]$Action = 'up',

    [ValidateSet('all', 'backend', 'frontend')]
    [string]$Service = 'all',

    [switch]$Follow
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$RootPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$BackendPath = Join-Path $RootPath 'backend'
$FrontendPath = Join-Path $RootPath 'frontend'
$DataPath = Join-Path $BackendPath 'data'
$VenvPath = Join-Path $RootPath '.venv'
$VenvPython = Join-Path $VenvPath 'Scripts\python.exe'
$RequirementsPath = Join-Path $BackendPath 'requirements.txt'
$RequirementsStamp = Join-Path $VenvPath '.requirements.stamp'
$DefaultWheelhousePath = Join-Path $BackendPath 'wheelhouse'
$WheelhouseSetting = [Environment]::GetEnvironmentVariable('DETOPS_WHEELHOUSE', 'Process')
$PackageJsonPath = Join-Path $FrontendPath 'package.json'
$PackageLockPath = Join-Path $FrontendPath 'package-lock.json'
$InstalledLockPath = Join-Path $FrontendPath 'node_modules\.package-lock.json'
$ViteEntryPath = Join-Path $FrontendPath 'node_modules\vite\bin\vite.js'

$BackendPidFile = Join-Path $DataPath 'backend.windows.pid.json'
$BackendOutLog = Join-Path $DataPath 'backend.windows.out.log'
$BackendErrLog = Join-Path $DataPath 'backend.windows.err.log'
$FrontendPidFile = Join-Path $FrontendPath '.frontend.windows.pid.json'
$FrontendOutLog = Join-Path $FrontendPath '.frontend.windows.out.log'
$FrontendErrLog = Join-Path $FrontendPath '.frontend.windows.err.log'
$RunModeFile = Join-Path $DataPath 'windows.run-mode'

$BackendPort = 8099
$BackendConfigurationErrors = @()
$backendPortText = [Environment]::GetEnvironmentVariable('DETOPS_PORT', 'Process')
if (-not [string]::IsNullOrWhiteSpace($backendPortText)) {
    $parsedBackendPort = 0
    if (-not [int]::TryParse($backendPortText.Trim(), [ref]$parsedBackendPort) -or
        $parsedBackendPort -lt 1 -or $parsedBackendPort -gt 65535) {
        $BackendConfigurationErrors += "Invalid DETOPS_PORT '$backendPortText'. Expected a number from 1 to 65535."
    }
    else {
        $BackendPort = $parsedBackendPort
        [Environment]::SetEnvironmentVariable(
            'DETOPS_PORT',
            $BackendPort.ToString([Globalization.CultureInfo]::InvariantCulture),
            'Process'
        )
    }
}

$ListenAddress = [Environment]::GetEnvironmentVariable('DETOPS_HOST', 'Process')
if ([string]::IsNullOrWhiteSpace($ListenAddress)) {
    $ListenAddress = '0.0.0.0'
}
else {
    $ListenAddress = $ListenAddress.Trim()
}
if ([Uri]::CheckHostName($ListenAddress) -eq [UriHostNameType]::Unknown) {
    $BackendConfigurationErrors += "Invalid DETOPS_HOST '$ListenAddress'. Expected an IPv4, IPv6, or DNS host name."
}
else {
    [Environment]::SetEnvironmentVariable('DETOPS_HOST', $ListenAddress, 'Process')
}

$BackendProbeAddress = $ListenAddress
if ($ListenAddress -eq '0.0.0.0') {
    $BackendProbeAddress = '127.0.0.1'
}
elseif ($ListenAddress -eq '::' -or $ListenAddress -eq '0:0:0:0:0:0:0:0') {
    $BackendProbeAddress = '::1'
}
$BackendUrlHost = $BackendProbeAddress
if ($BackendUrlHost.IndexOf(':') -ge 0) {
    $BackendUrlHost = "[$BackendUrlHost]"
}
$BackendBaseUrl = 'http://{0}:{1}' -f $BackendUrlHost, $BackendPort

$FrontendPort = 5178
$script:NodeCommand = $null
$script:NpmCommand = $null

function Assert-BackendConfiguration {
    if ($BackendConfigurationErrors.Count -gt 0) {
        throw ($BackendConfigurationErrors -join [Environment]::NewLine)
    }
}

function Test-EnabledEnvironmentFlag {
    param([string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) { return $false }
    return @('1', 'true', 'yes', 'on') -contains $value.Trim().ToLowerInvariant()
}

function Assert-BackendPackageAccess {
    $problems = @()
    if (Test-EnabledEnvironmentFlag 'PIP_NO_INDEX') {
        $problems += 'PIP_NO_INDEX is enabled, so pip is not allowed to query a package index.'
    }

    $blockedProxyNames = @()
    foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'Process')
        if (-not [string]::IsNullOrWhiteSpace($value) -and
            $value.Trim() -match '^https?://127\.0\.0\.1:9/?$') {
            $blockedProxyNames += $name
        }
    }
    if ($blockedProxyNames.Count -gt 0) {
        $problems += "$($blockedProxyNames -join ', ') points to the disabled placeholder proxy 127.0.0.1:9."
    }

    if ($problems.Count -gt 0) {
        $guidance = @(
            'Backend dependencies are missing, but the current pip network environment blocks downloads.',
            ($problems -join [Environment]::NewLine),
            'Fix the variables in this PowerShell window, then run scripts\start.cmd again:',
            '  Remove-Item Env:PIP_NO_INDEX -ErrorAction SilentlyContinue',
            '  Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:ALL_PROXY -ErrorAction SilentlyContinue',
            'If your network requires a proxy, set HTTP_PROXY and HTTPS_PROXY to the real proxy instead of removing them.'
        )
        throw ($guidance -join [Environment]::NewLine)
    }
}

function Get-WheelhousePath {
    if ([string]::IsNullOrWhiteSpace($WheelhouseSetting)) {
        return $DefaultWheelhousePath
    }

    $candidate = $WheelhouseSetting.Trim()
    try {
        if ([IO.Path]::IsPathRooted($candidate)) {
            return [IO.Path]::GetFullPath($candidate)
        }
        return [IO.Path]::GetFullPath((Join-Path $RootPath $candidate))
    }
    catch {
        throw "Invalid DETOPS_WHEELHOUSE path '$WheelhouseSetting'."
    }
}

function Find-Application {
    param([string[]]$Names)

    foreach ($name in $Names) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }
    throw "Required command not found: $($Names -join ', ')"
}

function Convert-ToVersion {
    param([string]$Text, [string]$Label)

    $clean = ($Text.Trim() -replace '^v', '')
    try {
        return [version]$clean
    }
    catch {
        throw "Could not parse $Label version '$Text'."
    }
}

function Assert-FrontendRuntime {
    if ($null -eq $script:NodeCommand) {
        $script:NodeCommand = Find-Application @('node.exe', 'node')
    }
    if ($null -eq $script:NpmCommand) {
        $script:NpmCommand = Find-Application @('npm.cmd')
    }

    $nodeLines = @(& $script:NodeCommand --version)
    $nodeExitCode = $LASTEXITCODE
    if ($nodeExitCode -ne 0 -or $nodeLines.Count -eq 0) { throw 'Could not run node --version.' }
    $nodeText = $nodeLines[0]
    $nodeVersion = Convert-ToVersion ([string]$nodeText) 'Node.js'
    if ($nodeVersion -lt [version]'16.20.2') {
        throw "Node.js $nodeVersion is too old. Version 16.20.2 or newer is required."
    }

    $npmLines = @(& $script:NpmCommand --version)
    $npmExitCode = $LASTEXITCODE
    if ($npmExitCode -ne 0 -or $npmLines.Count -eq 0) { throw 'Could not run npm --version.' }
    $npmText = $npmLines[0]
    $npmVersion = Convert-ToVersion ([string]$npmText) 'npm'
    if ($npmVersion -lt [version]'8.19.0') {
        throw "npm $npmVersion is too old. Version 8.19.0 or newer is required."
    }
}

function Invoke-CheckedCommand {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$Description
    )

    Push-Location $WorkingDirectory
    try {
        & $FilePath @ArgumentList
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "$Description failed with exit code $exitCode."
        }
    }
    finally {
        Pop-Location
    }
}

function Remove-StalePidFile {
    param([string]$PidFile)

    if (Test-Path -LiteralPath $PidFile) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
}

function Get-RunMode {
    if (-not (Test-Path -LiteralPath $RunModeFile)) { return 'dev' }
    $mode = (Get-Content -LiteralPath $RunModeFile -Raw -Encoding ASCII).Trim()
    if ($mode -eq 'prod') { return 'prod' }
    return 'dev'
}

function Set-RunMode {
    param([ValidateSet('dev', 'prod')][string]$Mode)

    New-Item -ItemType Directory -Path $DataPath -Force | Out-Null
    Set-Content -LiteralPath $RunModeFile -Value $Mode -Encoding ASCII
}

function Remove-RunMode {
    Remove-StalePidFile $RunModeFile
}

function Get-ManagedProcess {
    param([string]$PidFile, [string]$ExpectedKind)

    if (-not (Test-Path -LiteralPath $PidFile)) { return $null }

    try {
        $metadata = Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $processId = [int]$metadata.pid
        if ([string]$metadata.kind -ne $ExpectedKind) { throw 'PID kind mismatch.' }
        if (-not [string]::Equals([string]$metadata.root, $RootPath, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'PID root mismatch.'
        }

        $process = Get-Process -Id $processId -ErrorAction Stop
        $actualStart = $process.StartTime.ToUniversalTime()
        if ($null -ne $metadata.PSObject.Properties['startTimeUtcTicks']) {
            $recordedTicks = [long]::Parse(
                [string]$metadata.startTimeUtcTicks,
                [Globalization.CultureInfo]::InvariantCulture
            )
            if ($actualStart.Ticks -ne $recordedTicks) {
                throw 'PID start time mismatch.'
            }
        }
        else {
            $recordedStart = [datetime]::Parse(
                [string]$metadata.startTimeUtc,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            ).ToUniversalTime()
            if ([math]::Abs(($actualStart - $recordedStart).TotalSeconds) -gt 1.0) {
                throw 'PID start time mismatch.'
            }
        }

        if ($null -ne $metadata.PSObject.Properties['executablePath']) {
            $actualExecutable = $null
            try { $actualExecutable = $process.Path } catch { $actualExecutable = $null }
            if (-not [string]::IsNullOrWhiteSpace($actualExecutable) -and
                -not [string]::Equals(
                    [IO.Path]::GetFullPath($actualExecutable),
                    [IO.Path]::GetFullPath([string]$metadata.executablePath),
                    [StringComparison]::OrdinalIgnoreCase
                )) {
                throw 'PID executable mismatch.'
            }
        }

        return [pscustomobject]@{ Process = $process; Metadata = $metadata }
    }
    catch {
        Remove-StalePidFile $PidFile
        return $null
    }
}

function Save-ManagedProcess {
    param(
        [Diagnostics.Process]$Process,
        [string]$PidFile,
        [string]$Kind,
        [int]$Port,
        [string]$ExecutablePath,
        [string]$ReadyUrl
    )

    $startTimeUtc = $Process.StartTime.ToUniversalTime()
    $metadata = [ordered]@{
        pid               = $Process.Id
        startTimeUtc      = $startTimeUtc.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
        startTimeUtcTicks = $startTimeUtc.Ticks.ToString([Globalization.CultureInfo]::InvariantCulture)
        kind              = $Kind
        port              = $Port
        root              = $RootPath
        executablePath    = [IO.Path]::GetFullPath($ExecutablePath)
        readyUrl          = $ReadyUrl
    }
    $metadata | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8
}

function Stop-ManagedProcess {
    param(
        [string]$PidFile,
        [string]$Kind,
        [string]$DisplayName
    )

    $managed = Get-ManagedProcess $PidFile $Kind
    if ($null -eq $managed) {
        Write-Host "$DisplayName is not running."
        return
    }

    $processId = [int]$managed.Process.Id
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    & $taskkill /PID $processId /T /F 2>$null | Out-Null

    $deadline = [datetime]::UtcNow.AddSeconds(8)
    do {
        Start-Sleep -Milliseconds 200
        $remaining = Get-Process -Id $processId -ErrorAction SilentlyContinue
    } while ($null -ne $remaining -and [datetime]::UtcNow -lt $deadline)

    if ($null -ne $remaining) {
        throw "Could not stop $DisplayName process $processId."
    }
    Remove-StalePidFile $PidFile
    Write-Host "$DisplayName stopped."
}

function Test-LocalPort {
    param([int]$Port)

    $client = New-Object Net.Sockets.TcpClient
    $waitHandle = $null
    try {
        $asyncResult = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        $waitHandle = $asyncResult.AsyncWaitHandle
        if (-not $waitHandle.WaitOne(400, $false)) { return $false }
        $client.EndConnect($asyncResult)
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $waitHandle) { $waitHandle.Close() }
        $client.Close()
    }
}

function Test-HttpUrl {
    param([string]$Url)

    $response = $null
    try {
        $request = [Net.HttpWebRequest]::Create($Url)
        $request.Method = 'GET'
        $request.Proxy = $null
        $request.Timeout = 2000
        $request.ReadWriteTimeout = 2000
        $request.KeepAlive = $false
        $response = $request.GetResponse()
        $statusCode = [int]$response.StatusCode
        return $statusCode -ge 200 -and $statusCode -lt 400
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $response) { $response.Close() }
    }
}

function Wait-ForEndpoint {
    param(
        [string]$PidFile,
        [string]$Kind,
        [string]$Url,
        [int]$TimeoutSeconds
    )

    $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ($null -eq (Get-ManagedProcess $PidFile $Kind)) { return $false }
        if (Test-HttpUrl $Url) { return $true }
        Start-Sleep -Milliseconds 500
    } while ([datetime]::UtcNow -lt $deadline)
    return $false
}

function Write-LogTail {
    param([string[]]$Paths, [int]$Lines = 50)

    foreach ($path in $Paths) {
        if (Test-Path -LiteralPath $path) {
            Write-Host "--- $path"
            Get-Content -LiteralPath $path -Tail $Lines -Encoding UTF8
        }
    }
}

function Get-BasePython {
    $candidates = @()
    $launcher = Get-Command 'py.exe' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        $candidates += [pscustomobject]@{ Path = $launcher.Source; Prefix = @('-3') }
    }
    $python = Get-Command 'python.exe' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        $candidates += [pscustomobject]@{ Path = $python.Source; Prefix = @() }
    }

    foreach ($candidate in $candidates) {
        $checkArgs = @($candidate.Prefix) + @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)')
        & $candidate.Path @checkArgs 2>$null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    throw 'Python 3.9 or newer was not found. Install Python and enable the py launcher or python.exe on PATH.'
}

function Ensure-BackendDependencies {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Host 'Creating Python virtual environment...'
        $basePython = Get-BasePython
        $createArgs = @($basePython.Prefix) + @('-m', 'venv', $VenvPath)
        Invoke-CheckedCommand $basePython.Path $createArgs $RootPath 'Python virtual environment creation'
    }

    & $VenvPython -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "The virtual environment at '$VenvPath' does not contain Python 3.9 or newer."
    }

    $needsInstall = -not (Test-Path -LiteralPath $RequirementsStamp)
    if (-not $needsInstall) {
        $needsInstall = (Get-Item -LiteralPath $RequirementsPath).LastWriteTimeUtc -gt
            (Get-Item -LiteralPath $RequirementsStamp).LastWriteTimeUtc
    }
    if (-not $needsInstall) {
        & $VenvPython -c 'import fastapi, uvicorn' 2>$null
        $needsInstall = $LASTEXITCODE -ne 0
    }

    if ($needsInstall) {
        $wheelhousePath = Get-WheelhousePath
        $wheelhouseExists = Test-Path -LiteralPath $wheelhousePath -PathType Container
        if (-not $wheelhouseExists -and -not [string]::IsNullOrWhiteSpace($WheelhouseSetting)) {
            throw "DETOPS_WHEELHOUSE does not exist or is not a directory: $wheelhousePath"
        }

        if ($wheelhouseExists) {
            $wheelFiles = @(Get-ChildItem -LiteralPath $wheelhousePath -Filter '*.whl' -File)
            if ($wheelFiles.Count -eq 0) {
                throw "The offline wheelhouse is empty: $wheelhousePath"
            }
            Write-Host "Installing/updating backend dependencies from offline wheelhouse: $wheelhousePath"
            $installArgs = @(
                '-m', 'pip', 'install', '--disable-pip-version-check',
                '--no-index', '--find-links', $wheelhousePath,
                '-r', $RequirementsPath
            )
        }
        else {
            Assert-BackendPackageAccess
            Write-Host 'Installing/updating backend dependencies from the configured package index...'
            $installArgs = @(
                '-m', 'pip', 'install', '--disable-pip-version-check',
                '-r', $RequirementsPath
            )
        }

        Invoke-CheckedCommand $VenvPython $installArgs $RootPath 'Backend dependency installation'
        Set-Content -LiteralPath $RequirementsStamp -Value ([datetime]::UtcNow.ToString('o')) -Encoding ASCII
    }
}

function Ensure-FrontendDependencies {
    Assert-FrontendRuntime

    $needsInstall = -not (Test-Path -LiteralPath $PackageLockPath) -or
        -not (Test-Path -LiteralPath $ViteEntryPath) -or
        -not (Test-Path -LiteralPath $InstalledLockPath)
    if (-not $needsInstall) {
        $installedTime = (Get-Item -LiteralPath $InstalledLockPath).LastWriteTimeUtc
        $needsInstall = (Get-Item -LiteralPath $PackageJsonPath).LastWriteTimeUtc -gt $installedTime -or
            (Get-Item -LiteralPath $PackageLockPath).LastWriteTimeUtc -gt $installedTime
    }

    if ($needsInstall) {
        Write-Host 'Installing/updating frontend dependencies...'
        $oldSkip = [Environment]::GetEnvironmentVariable('PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD', 'Process')
        [Environment]::SetEnvironmentVariable('PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD', '1', 'Process')
        try {
            if (Test-Path -LiteralPath $PackageLockPath) {
                Invoke-CheckedCommand $script:NpmCommand @('ci', '--no-audit', '--no-fund') $FrontendPath 'Frontend dependency installation'
            }
            else {
                Invoke-CheckedCommand $script:NpmCommand @('install', '--no-audit', '--no-fund') $FrontendPath 'Frontend dependency installation'
            }
        }
        finally {
            [Environment]::SetEnvironmentVariable('PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD', $oldSkip, 'Process')
        }
    }
}

function Start-Backend {
    Assert-BackendConfiguration
    New-Item -ItemType Directory -Path $DataPath -Force | Out-Null
    $existing = Get-ManagedProcess $BackendPidFile 'backend'
    if ($null -ne $existing) {
        Write-Host "Backend is already running (PID $($existing.Process.Id), port $($existing.Metadata.port))."
        return
    }
    if (Test-LocalPort $BackendPort) {
        throw "Backend port $BackendPort is already in use by a process not managed by this script."
    }

    Ensure-BackendDependencies
    Write-Host "Starting backend on $ListenAddress`:$BackendPort..."
    $oldUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'Process')
    $oldUnbuffered = [Environment]::GetEnvironmentVariable('PYTHONUNBUFFERED', 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED', '1', 'Process')
    try {
        $process = Start-Process -FilePath $VenvPython `
            -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', $ListenAddress, '--port', "$BackendPort") `
            -WorkingDirectory $BackendPath -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $BackendOutLog -RedirectStandardError $BackendErrLog
    }
    finally {
        [Environment]::SetEnvironmentVariable('PYTHONUTF8', $oldUtf8, 'Process')
        [Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED', $oldUnbuffered, 'Process')
    }

    $healthUrl = "$BackendBaseUrl/api/health"
    try {
        Save-ManagedProcess $process $BackendPidFile 'backend' $BackendPort $VenvPython $healthUrl
    }
    catch {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw
    }

    if (Wait-ForEndpoint $BackendPidFile 'backend' $healthUrl 45) {
        Write-Host "Backend is ready: $healthUrl"
        return
    }
    if ($null -eq (Get-ManagedProcess $BackendPidFile 'backend')) {
        Write-LogTail @($BackendOutLog, $BackendErrLog)
        throw "Backend exited during startup. See logs above."
    }
    Write-Warning "Backend process is running but health check is not ready yet. Logs: $BackendOutLog, $BackendErrLog"
}

function Start-Frontend {
    Assert-BackendConfiguration
    $existing = Get-ManagedProcess $FrontendPidFile 'frontend'
    if ($null -ne $existing) {
        Write-Host "Frontend is already running (PID $($existing.Process.Id), port $($existing.Metadata.port))."
        return
    }
    if (Test-LocalPort $FrontendPort) {
        throw "Frontend port $FrontendPort is already in use by a process not managed by this script."
    }

    Ensure-FrontendDependencies
    Write-Host "Starting Vite frontend on 0.0.0.0`:$FrontendPort..."
    $process = Start-Process -FilePath $script:NodeCommand `
        -ArgumentList @('node_modules/vite/bin/vite.js', '--host', '0.0.0.0', '--port', "$FrontendPort", '--strictPort') `
        -WorkingDirectory $FrontendPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $FrontendOutLog -RedirectStandardError $FrontendErrLog

    $viteUrl = "http://127.0.0.1:$FrontendPort/@vite/client"
    try {
        Save-ManagedProcess $process $FrontendPidFile 'frontend' $FrontendPort $script:NodeCommand $viteUrl
    }
    catch {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw
    }

    if (Wait-ForEndpoint $FrontendPidFile 'frontend' $viteUrl 30) {
        Write-Host "Frontend is ready: http://127.0.0.1:$FrontendPort"
        return
    }
    if ($null -eq (Get-ManagedProcess $FrontendPidFile 'frontend')) {
        Write-LogTail @($FrontendOutLog, $FrontendErrLog)
        throw 'Frontend exited during startup. See logs above.'
    }
    Write-Warning "Frontend process is running but HTTP is not ready yet. Logs: $FrontendOutLog, $FrontendErrLog"
}

function Stop-Backend { Stop-ManagedProcess $BackendPidFile 'backend' 'Backend' }
function Stop-Frontend { Stop-ManagedProcess $FrontendPidFile 'frontend' 'Frontend' }

function Build-Frontend {
    Ensure-FrontendDependencies
    Write-Host 'Building frontend...'
    Invoke-CheckedCommand $script:NpmCommand @('run', 'build') $FrontendPath 'Frontend build'
    Write-Host "Frontend build completed: $(Join-Path $FrontendPath 'dist')"
}

function Show-ServiceStatus {
    param(
        [string]$PidFile,
        [string]$Kind,
        [string]$DisplayName,
        [string]$ReadyPath
    )

    $managed = Get-ManagedProcess $PidFile $Kind
    if ($null -eq $managed) {
        Write-Host "${DisplayName}: stopped"
        return $false
    }

    $recordedPort = [int]$managed.Metadata.port
    if ($null -ne $managed.Metadata.PSObject.Properties['readyUrl']) {
        $url = [string]$managed.Metadata.readyUrl
    }
    else {
        $url = "http://127.0.0.1:$recordedPort$ReadyPath"
    }
    if (Test-HttpUrl $url) {
        Write-Host "${DisplayName}: running (PID $($managed.Process.Id), port $recordedPort, ready)"
        return $true
    }
    Write-Host "${DisplayName}: running (PID $($managed.Process.Id), port $recordedPort, not ready)"
    return $false
}

function Show-SelectedStatus {
    param([string]$SelectedService)

    if ($SelectedService -eq 'backend') {
        return Show-ServiceStatus $BackendPidFile 'backend' 'Backend' '/api/health'
    }
    if ($SelectedService -eq 'frontend') {
        return Show-ServiceStatus $FrontendPidFile 'frontend' 'Frontend' '/@vite/client'
    }

    $backendOk = Show-ServiceStatus $BackendPidFile 'backend' 'Backend' '/api/health'
    if ((Get-RunMode) -eq 'prod') {
        Write-Host 'Frontend: not required (production build is served by the backend)'
        return $backendOk
    }
    $frontendOk = Show-ServiceStatus $FrontendPidFile 'frontend' 'Frontend' '/@vite/client'
    return $backendOk -and $frontendOk
}

function Show-SelectedLogs {
    param([string]$SelectedService, [bool]$WaitForChanges)

    $paths = @()
    if ($SelectedService -eq 'all' -or $SelectedService -eq 'backend') {
        $paths += @($BackendOutLog, $BackendErrLog)
    }
    if ($SelectedService -eq 'all' -or $SelectedService -eq 'frontend') {
        $paths += @($FrontendOutLog, $FrontendErrLog)
    }
    $existingPaths = @($paths | Where-Object { Test-Path -LiteralPath $_ })
    if ($existingPaths.Count -eq 0) {
        Write-Host 'No log files exist yet.'
        return
    }

    Write-LogTail $existingPaths 80
    if ($WaitForChanges) {
        Write-Host 'Following logs. Press Ctrl+C to stop.'
        Get-Content -LiteralPath $existingPaths -Tail 0 -Wait -Encoding UTF8
    }
}

function Start-Selected {
    param([string]$SelectedService)

    if ($SelectedService -eq 'all' -or $SelectedService -eq 'backend') { Start-Backend }
    if ($SelectedService -eq 'all' -or $SelectedService -eq 'frontend') { Start-Frontend }
}

function Stop-Selected {
    param([string]$SelectedService)

    $stopErrors = @()
    if ($SelectedService -eq 'all' -or $SelectedService -eq 'frontend') {
        try { Stop-Frontend } catch { $stopErrors += $_.Exception.Message }
    }
    if ($SelectedService -eq 'all' -or $SelectedService -eq 'backend') {
        try { Stop-Backend } catch { $stopErrors += $_.Exception.Message }
    }
    if ($stopErrors.Count -gt 0) {
        throw ($stopErrors -join [Environment]::NewLine)
    }
}

try {
    switch ($Action) {
        'up' {
            if ($Service -eq 'all') { Remove-RunMode }
            Start-Selected $Service
            if ($Service -eq 'all') {
                Set-RunMode 'dev'
                Write-Host "Open http://127.0.0.1:$FrontendPort"
            }
        }
        'start' {
            if ($Service -eq 'all') { Remove-RunMode }
            Start-Selected $Service
            if ($Service -eq 'all') {
                Set-RunMode 'dev'
                Write-Host "Open http://127.0.0.1:$FrontendPort"
            }
        }
        'prod' {
            if ($Service -ne 'all') { throw 'The prod action does not accept -Service.' }
            Stop-Frontend
            Build-Frontend
            Stop-Backend
            Start-Backend
            Set-RunMode 'prod'
            Write-Host "Production mode is ready at $BackendBaseUrl"
        }
        'down' {
            Stop-Selected $Service
            if ($Service -eq 'all') { Remove-RunMode }
        }
        'stop' {
            Stop-Selected $Service
            if ($Service -eq 'all') { Remove-RunMode }
        }
        'restart' {
            if ($Service -eq 'all') { Remove-RunMode }
            Stop-Selected $Service
            Start-Selected $Service
            if ($Service -eq 'all') {
                Set-RunMode 'dev'
                Write-Host "Open http://127.0.0.1:$FrontendPort"
            }
        }
        'status' {
            $statusOk = Show-SelectedStatus $Service
            if (-not $statusOk) { exit 1 }
        }
        'build' {
            if ($Service -eq 'backend') { throw 'The build action is only available for the frontend.' }
            Build-Frontend
        }
        'logs' { Show-SelectedLogs $Service ([bool]$Follow) }
    }
}
catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}

exit 0
