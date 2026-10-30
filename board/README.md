# 板端运行

平台：TUL PYNQ-Z2 / xc7z020clg400-1，Waveshare ADS1256/DAC8552 AD/DA模块，BE03位流。板端需要PYNQ Python、NumPy、Pillow、HDMI与USB触摸环境。`application/`是已在实板自动启动的v6文件，内附逐文件哈希。

## 准备与启动

1. 按`src/battery_eis/docs/final_assembly_log_20261021.md`核对装配：R1=10Ω/5W、R2=0.1Ω、R5=2.49kΩ。J1是F+、S+、S−、F−；J2是AD0、AD1、AD2、AD3、DA0、AGND。AD3/F−不能另短接AGND。当前程序收窄到3.0—3.7V、0.4A，不按PCB的5V讨论上限直接放行。
2. 从本机已有验证目录取得`profile.pending.json`与`prototype_calibration.json`，保留原文件。新机器需先独立完成接线、零激励和交流幅相校准；`data/fixtures/`是回放样例，不是新设备校准。
3. 将仓库复制到板端。以下命令只创建一个不存在的新目录，并校验旧冻结基础文件、当前应用与校准绑定；不加载FPGA、不写systemd、不改网络。用实际路径替换两个输入参数：

```sh
/usr/local/share/pynq-venv/bin/python3 -B board/prepare_runtime.py \
  --output /home/xilinx/battery_eis_runtime \
  --profile /path/to/verified/profile.pending.json \
  --calibration /path/to/verified/prototype_calibration.json
```

4. 确认没有其他程序占用FPGA/ADDA，电池断开后使用同一PYNQ环境手动启动：

```sh
sudo env XILINX_XRT=/usr OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  /usr/local/share/pynq-venv/bin/python3 -B -u \
  /home/xilinx/battery_eis_runtime/battery_ui_run_records_20261030_v6/prototype_start.py \
  --root /home/xilinx/battery_eis_runtime
```

开机默认停止测量，先验证零激励再接入电池。首页“寿命测量”完成三频重复采集；设置页“开始单点”只连续测电压、电流、幅相。停止程序时保留其输出归零清理流程。

## 自动启动及回退

`battery-eis-panel.service.example`给出本项目PYNQ环境的参考unit。已有设备应沿用其经过验证的服务与版本目录切换流程，先备份原unit、drop-in和启用状态；不要创建两个同时加载FPGA的服务。新设备手动通过后，核对Python路径、实际启动依赖和runtime目录，再安装/启用unit。

验收要检查当前InvocationID日志，包含`UI_RECORDS_RUN_START count=0`与`UI_FRAME_READY pages=3`；`runtime.current.log`只保留本次程序运行，系统journal保留。重启后记录列表空，原始测量保存在runtime根目录`data/`。回退时停用当前入口，恢复备份unit/drop-in与原启用状态，然后启动原版本；不覆盖校准和profile。

本机v6已完成一次Linux重启自动启动验证，证据在`data/boot_20261030/`。冷断电验证未进行。源码包不包含设备凭据或当前私人校准；历史fixture仅供离线测试。
