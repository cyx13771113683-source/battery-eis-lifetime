param([string]$VivadoBin='D:\software\vivado2025.2\2025.2\Vivado\bin')
$ErrorActionPreference='Stop'
$base=Split-Path $PSScriptRoot
$out=Join-Path $base 'build/sim_contract'
New-Item -ItemType Directory -Force -Path $out | Out-Null
Copy-Item (Join-Path $base 'rtl/sine14.mem') $out -Force
Push-Location $out
try {
 & "$VivadoBin/xvlog.bat" --sv "$base/rtl/eis_moments.sv" "$base/rtl/battery_adc_engine.sv" "$base/rtl/battery_eis_core.sv" "$base/sim/tb_battery_eis.sv" "$base/sim/tb_spi_contract.sv" 2>&1 | Tee-Object compile.log
 if($LASTEXITCODE){throw 'RTL compile failed'}
 & "$VivadoBin/xelab.bat" tb_spi_contract -s battery_contract -timescale 1ns/1ps 2>&1 | Tee-Object elaborate.log
 if($LASTEXITCODE){throw 'elaboration failed'}
 & "$VivadoBin/xsim.bat" battery_contract -runall 2>&1 | Tee-Object run.log
 if($LASTEXITCODE){throw 'simulation failed'}
 $log=Get-Content -LiteralPath run.log -Raw
 if($log -notmatch 'PASS_BATTERY_EIS' -or $log -notmatch 'PASS_SPI_CONTRACT' -or $log -match 'Fatal:|Error:|FAIL_'){throw 'simulation contract failed'}
 & "$VivadoBin/xsim.bat" battery_contract -runall -testplusarg INJECT_SHORT_RDATA_WAIT 2>&1 | Tee-Object negative_short_wait.log
 $negative=Get-Content -LiteralPath negative_short_wait.log -Raw
 if($negative -notmatch 'Fatal: FAIL_CONTRACT_T6' -or $negative -match 'PASS_SPI_CONTRACT|PASS_BATTERY_EIS'){throw 'negative timing control did not fail as expected'}
 Write-Output 'PASS_NEGATIVE_CONTROL_SHORT_RDATA_WAIT'
 foreach($name in @('compile.log','elaborate.log','run.log','negative_short_wait.log')) {
  $lines=Get-Content -LiteralPath $name
  $lines | Set-Content -LiteralPath $name -Encoding UTF8
 }
} finally {Pop-Location}
