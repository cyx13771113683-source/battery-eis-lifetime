param([Parameter(Mandatory=$true)][string]$VivadoBin,[Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
$repo=Split-Path $PSScriptRoot
$battery=Join-Path $repo 'src/battery_eis'
foreach($script in @('simulate.ps1','simulate_lowfreq.ps1','simulate_contract.ps1')) {
 & (Join-Path $battery "scripts/$script") -VivadoBin $VivadoBin
 if($LASTEXITCODE){throw "Simulation failed: $script"}
}
& (Join-Path $VivadoBin 'vivado.bat') -mode batch -source "$PSScriptRoot/build.tcl" -tclargs "$PSScriptRoot/base.bd" $battery $Output
if($LASTEXITCODE){throw 'Vivado build failed'}
