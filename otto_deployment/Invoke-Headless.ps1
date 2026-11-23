param(
    [Parameter(Mandatory)][string]$Executable,
    [Parameter(Mandatory)][string[]]$Arguments,
    [string]$WorkingDirectory = (Get-Location).Path
)
$ErrorActionPreference = 'Stop'
$start = [System.Diagnostics.ProcessStartInfo]::new(
    (Get-Command $Executable -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
)
$start.WorkingDirectory = $WorkingDirectory
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
$process = [System.Diagnostics.Process]::Start($start)
$errors = $process.StandardError.ReadToEndAsync()
while (($line = $process.StandardOutput.ReadLine()) -ne $null) {
    Write-Output $line
}
$process.WaitForExit()
$errorText = $errors.GetAwaiter().GetResult()
if ($errorText) { [Console]::Error.Write($errorText) }
$code = $process.ExitCode
$process.Dispose()
exit $code
