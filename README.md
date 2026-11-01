# 基于 FPGA EIS 的锂电池寿命评估

2026 AMD FPGA 创新设计赛道，初级组自主选题。目标平台：TUL PYNQ-Z2，Zynq-7020 (`xc7z020clg400-1`)；FPGA 工具：Vivado 2025.2。

系统通过专用负载板和 ADS1256/DAC8552 前端采集锂电池电压、电流，由 PL 完成时间戳与锁相矩累积，PS 计算复阻抗并调用电池健康度模型。操作界面为首页、设置、记录三页。

设置页提供连续单点测量和频点选择；首页“寿命测量”自动采集80、17.8、2.16Hz，完成后恢复测量前的单点运行状态。首页幅值、相位曲线各保留三个频点的最新数据，显示缓存不参与SOH计算。记录页只显示本次程序启动后有SOH数值的结果，重启列表清空，原始测量文件保留。详情页显示SOH、两张曲线和返回按钮。

当前应用为 **2026-10-31 v10**，与2026-11-01的20颗实测版本一致。完整三频作业平均18.16 s，20颗均完成；原始采集和两次首次失败记录见[data/batch_20](data/batch_20/README.md)。负责人确认20颗均经专业设备完整充放电容量对照，SOH最大误差不超过5%，详细来源和适用范围见[验证说明](report/validation.md)。

完整报告：[技术文档 Word](report/technical_report.docx)。原文件按负责人完成版本原样上传，正文未改动。

## 目录

| 目录 | 材料 |
|---|---|
| `src/battery_eis/` | 电池应用、模型、RTL、配置、字库、测试和仿真平台 |
| `sim/` | 仿真方法、历史原始仿真证据和本次软件验证 |
| `build/` | 可移植构建入口、BD、必需 IP、约束及综合实现报告 |
| `board/` | BE03 BIT/HWH/XSA、实板v10运行文件、运行目录准备脚本与说明 |
| `data/` | 20颗实测原始数据、公开模型回放样例及配置模板 |
| `skill/` | 从测量与推理流程提炼的通用经验 |
| `report/` | 设计、验证、比赛核对及协作记录 |

## 离线运行

在仓库根目录，使用 Python 3.10 或更新版本：

```sh
python -m pip install -r requirements.txt
python -B -m unittest discover -s src/battery_eis/tests
python -B sim/ui_functional_test.py
python -B sim/verify_runtime_package.py
python -B sim/verify_batch.py
python -B src/battery_eis/ps/app.py simulate --profile data/profile.example.json --output outputs/simulation
python -B src/battery_eis/ps/app.py replay-model --model src/battery_eis/models/frozen_two_frequency_20261022/lib_two_ridge.json --input data/lib_two_ridge.example.json --output outputs/public_replay.json
python -B src/battery_eis/ps/app.py preview --output outputs/preview
```

公开数据回放输出与实际电池采集的来源分别记录。模型接口目前返回的 RUL 为 null；寿命评估中的定量输出取决于所选模型及有效输入，不能将 SOH 百分比直接换算为剩余循环次数。

## FPGA 构建和上板

在短的英文路径克隆仓库，准备 Vivado 2025.2：

```powershell
./build/build.ps1 -VivadoBin 'C:/AMD/Vivado/2025.2/bin' -Output 'C:/fpga/battery_eis_build'
```

输出目录须不存在。脚本先执行三个 RTL 仿真入口，再综合、实现、检查时序和 DRC，导出 BIT/HWH/XSA。原始报告见 `build/reports/`。上板流程见 [board/README.md](board/README.md)。
