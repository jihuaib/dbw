#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs')]
    [string]$Action = 'start',
    [switch]$Follow
)

& (Join-Path $PSScriptRoot 'start.ps1') -Action $Action -Service backend -Follow:$Follow
exit $LASTEXITCODE
