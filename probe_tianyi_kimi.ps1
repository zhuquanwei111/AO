$ErrorActionPreference = 'Stop'
$sharedKey = [Environment]::GetEnvironmentVariable('TIANYI_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($sharedKey)) { $sharedKey = $env:TIANYI_API_KEY }
if ([string]::IsNullOrWhiteSpace($sharedKey)) {
    Write-Host 'TIANYI_API_KEY is not available; no credential was changed.'
    exit 3
}

$env:TIANYI_API_KEY = $sharedKey
$env:Path += ';' + (Join-Path $env:USERPROFILE '.kimi-code\bin')
$psi = [Diagnostics.ProcessStartInfo]::new()
$psi.FileName = Join-Path $env:USERPROFILE '.kimi-code\bin\kimi.exe'
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
foreach ($arg in @('--model', 'tianyi/kimi-k3', '--prompt', 'Reply exactly KIMI_CLI_READY', '--output-format', 'text')) {
    $psi.ArgumentList.Add($arg)
}

$process = [Diagnostics.Process]::new()
$process.StartInfo = $psi
try {
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $exitCode = $process.ExitCode
    $markerMatched = ($stdout + "`n" + $stderr).Contains('KIMI_CLI_READY')
} finally {
    $process.Dispose()
    $stdout = $null
    $stderr = $null
    $sharedKey = $null
}

if ($exitCode -eq 0 -and $markerMatched) {
    Write-Host 'Kimi Code CLI probe: succeeded with tianyi/kimi-k3.'
    exit 0
}
Write-Host "Kimi Code CLI probe: failed exit_code=$exitCode; diagnostic content withheld."
exit 5
