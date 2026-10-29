# 验证入口

仓库根目录运行 `python -B -m unittest discover -s src/battery_eis/tests`。

Vivado RTL 验证脚本位于 `src/battery_eis/scripts/simulate.ps1`、`simulate_lowfreq.ps1`、`simulate_contract.ps1`，通过 `-VivadoBin` 指定安装位置。测试覆盖保护、长缓存和 SPI 契约；低频缓存仿真采用缩放时钟/ADC桩，实际采样以实物记录为准。

`evidence/` 保存原BE03历史仿真，当前软件验证另存本目录。综合入口见 `build/build.ps1`。
