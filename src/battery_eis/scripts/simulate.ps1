param([string]$VivadoBin='D:\software\vivado2025.2\2025.2\Vivado\bin')
$ErrorActionPreference='Stop'
$base=Split-Path $PSScriptRoot
$out=Join-Path $base 'build/sim'
New-Item -ItemType Directory -Force -Path $out | Out-Null
Copy-Item (Join-Path $base 'rtl/sine14.mem') $out -Force
Push-Location $out
try {
 & "$VivadoBin/xvlog.bat" --sv "$base/rtl/eis_moments.sv" "$base/rtl/battery_adc_engine.sv" "$base/rtl/battery_eis_core.sv" "$base/sim/tb_battery_eis.sv" 2>&1 | Tee-Object compile.log
 if($LASTEXITCODE){throw 'RTL compile failed'}
 & "$VivadoBin/xelab.bat" tb_battery_eis -s battery_sim -timescale 1ns/1ps 2>&1 | Tee-Object elaborate.log
 if($LASTEXITCODE){throw 'elaboration failed'}
 & "$VivadoBin/xsim.bat" battery_sim -runall 2>&1 | Tee-Object run.log
 if($LASTEXITCODE -or !(Select-String -LiteralPath run.log -Pattern 'PASS_BATTERY_EIS' -Quiet) -or (Select-String -LiteralPath run.log -Pattern 'Fatal:|Error:|FAIL_' -Quiet)){throw 'simulation contract failed'}
 foreach($name in @('compile.log','elaborate.log','run.log')) {
  $lines=Get-Content -LiteralPath $name
  $lines | Set-Content -LiteralPath $name -Encoding UTF8
 }
} finally {Pop-Location}
