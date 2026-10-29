# BE03 校准说明

当前已扩展到2–100Hz、最长4秒；默认2.16/17.8Hz使用各自窗口，见 `be03_lowfreq_model.md`。旧BE02校准binding不被本版接受。1Hz及高于100Hz仍不支持采集。实际设置以本版程序和重新生成的模板为准。

# 校准与外置 10 mΩ 核验

## 三种不同证据

1. ADS1256 `SELFCAL`：上电初始化执行，保存寄存器和 OFC/FSC。它只校准 ADC 内部，不能校准分压、分流、RC、接触或多路时差。
2. 独立 DC 与复数传递校准：测出两条通道的实际比例和相位。DC 系数在 profile 中；AC 系数通过 `calibrate-transfer` 建立。所有修正必须有独立参考来源及不确定度。
3. 外置标准 A/B：验证 `Z_B−Z_A` 是否接近独立测得的标准阻抗。它不能同时辨识电压、电流两条通道的复增益，因此不自动生成传递校准文件。

## DC / AC 传递校准

本版按最终 R1=10 Ω、R2=0.100 Ω、R5=2.49 kΩ、还原系数 1.249 装配重新校准。制造时期、68 Ω/33 Ω 方案以及旧 BE01 的校准均不可迁用。先使用已知限流源和独立仪表，在实际连接方式下校准电压 DC、电流 DC，记录实际 R2、分压比、`voltage_dc_scale`、`current_dc_scale`、`dc_reference_source`。两项 DC scale 均乘在名义换算后的读数上。改变这些值会改变 binding，已有 AC 校准因此失效。应在有效电压/电流范围内多点验证。名义阻值不能代替实测。

每个频点用同一相位参考获得独立参考仪器的 `V_ac` 与 `I_ac`，同时保存本板未校准复幅值。参考仪器与本板必须共用可追溯的相位基准/时间同步，不能任意比较两个自由运行仪器的绝对相角。电流正方向与余弦参考一致。建立 `Cv=Vref/Vmeas`、`Ci=Iref/Imeas`，之后 `Z=−Cv·Vmeas/(Ci·Imeas)`。

## 1 Hz–10 kHz 多频点校准目标（2026-10-22）

目标频点为 **1、2、5、10、20、40、50、80、100、200、500、1000、2000、3200、5000、8000、10000 Hz**，共 17 点。基础网格按 1/2/5 每十倍频程展开，保留既有 40/80 Hz，在电流/电压模拟 RC 极点附近增加 3.2/8 kHz。每一点分别保存 `Cv(f)` 和 `Ci(f)`；100 Hz 的相位修正不能用于整个频带。50/100 Hz 附近还需独立检查工频干扰。

**这是校准目标和待填模板，尚未完成全频带采集或实测标定。** 当前 BE03 的 PS 和 RTL 允许2–100Hz；17点中2/5/10/20/40/50/80/100Hz共8点通过设置检查，默认双频2.16/17.8Hz另有专用模板，仍需最终装配、真实设备身份及参考实测才能形成有效校准。不能把“设置允许”解释成已验证的测量带宽。

| 范围 | 当前约束与后续验证内容 |
|---|---|
| 1 Hz | 8周期需要约8秒，超过BE03的4秒窗口与16384条原始RAM，仍不支持。 |
| 2–10 Hz | 按实际DDS频率自动分配至少8周期及至少1周期稳定时间。2.16Hz采集约3.6946秒；时间分配、基波提取和数据量通过数字测试，电化学稳定时间待实测。 |
| 10–100 Hz | 可生成精确 binding，逐点实测两通道复数系数，并在不同幅度下确定有效范围。默认测量扫频为2.16/17.8Hz；其他诊断点通过CLI显式选择。 |
| >100 Hz–10 kHz | 超出当前 PS/RTL 守卫。当前名义采样率约 1525.88 点/秒/通道，DAC 仲裁还会推迟部分槽。现有方波及估计器没有经过高频混叠、通道切换、激励实际幅相和相干欠采样验证，不能仅放宽频率数字。 |

[TI ADS1256 数据手册表 12–14](https://www.ti.com/lit/ds/symlink/ads1256.pdf) 给出的 30 kS/s 是连续转换数据率；30 kS/s 设置下理想 MUX 轮换总吞吐约 4374 次/秒，连续数字滤波器的 −3 dB 带宽约 6106 Hz。当前逐点切换实现低于该理想吞吐。6106 Hz 也不是绝对截止频率，不能仅以它断言 10 kHz 永远不可测；必须验证新的采样/激励方式、幅相响应、混叠和噪声代价后，才能启用高频点。当前不改变采样硬件或开放这些点。

仅考虑最终装配的名义模拟 RC，令 `Rv=2490||10000 Ω`、`Ri=4980 Ω`、`C=10 nF`，`Hv=1/(1+j2πfRvC)`、`Hi=1/(1+j2πfRiC)`。下表已扣除名义 DC 分压；不含 ADC 滤波、输入负载、时差、寄生和器差，**不能作为可导入的校准系数**：

| 频率 | 电压幅值比 | 电流幅值比 | 电压相位 − 电流相位 |
|---|---:|---:|---:|
| 100 Hz | 0.999922 | 0.999511 | +1.0746° |
| 1 kHz | 0.992246 | 0.954371 | +10.2353° |
| 3.2 kHz | 0.928209 | 0.706651 | +23.1942° |
| 5 kHz | 0.847500 | 0.538561 | +25.3551° |
| 10 kHz | 0.623900 | 0.304420 | +20.8782° |

10 kHz 时电流 RC 单独的反向增益就约 3.285，超过现有校准系数幅度上限 2；ADC 响应还未计入。后续高频方案需用实测误差/噪声证据重新确定增益范围，不能单纯扩大上限。

### 生成模板与检查覆盖

以下命令仅处理本地 JSON，不加载位流或启动负载。在 `battery_eis` 目录执行：

```bash
# 默认生成完整 17 点目标表；超出当前实现的点 binding=null，并写明 blocked_reason。
python3 ps/app.py calibration-template --profile config/board.json --output reference_1hz_10khz.json

# 生成默认双频待填表，使用实际板卡 profile。
python3 ps/app.py calibration-template --profile config/board.json --sweep 2.16 17.8 --output reference_supported.json
```

随源码提供 `config/calibration_1hz_10khz.template.json` 和 `config/calibration_supported.template.json`，由示例 profile 生成，仅供看格式。实际使用需用已完成 DC 校准和真实硬件哈希的 profile 重新生成；逐行核对本次真实 `_capture.json` 的 binding。`actual_frequency_hz` 是本次可支持频点的 NCO 实际频率；参考仪器应对齐该频率及共同相位基准。

模板初始 `evidence_kind=pending_reference`，所有实测复幅值、范围和不确定度均为 null。填入真实且可追溯的数据后，设置唯一 `id`、`reference_source`，并将 `evidence_kind` 改为 `independent_reference`。含未支持频点或空值的文件会被拒绝，工具不会跳过这些行后声称全频带已校准。

```bash
python3 ps/app.py calibrate-transfer --input reference_supported.json --output config/transfer_multifrequency.json
python3 ps/app.py calibration-check --profile config/board.json --calibration config/transfer_multifrequency.json --sweep 2.16 17.8 --output coverage_supported.json
# 省略 --sweep 会检查完整 1 Hz–10 kHz 目标网格，目前应明确报告未覆盖。
python3 ps/app.py calibration-check --profile config/board.json --calibration config/transfer_multifrequency.json --output coverage_full_target.json
```

覆盖报告区分 `matched`、`missing_calibration`、`unsupported_acquisition`，并显示已匹配的电压/电流增益和相位。命令退出码 0 表示所请求频点全部精确匹配，2 表示存在未覆盖点；输入格式错误仍报错。覆盖检查只检查 binding；实际电压/电流包络和质量须在采集后验证。测量入口在新会话和激励开始前一次报告全部缺失校准频点，不采用最近点、插值或外推。

冻结校准后，在每个使用频点用新采集和独立参考复核，并增加校准点之间的验证频率以检查变化趋势；验证频率若要输出已校准结果，也需先取得它自己的系数。记录真实误差与不确定度后，才能评估是否需要更密的校准网格。

### 原始参考记录格式

输入结构如下；省略的 `binding` 必须逐字段复制该次 `_capture.json` 中的真实对象，示例数值不能当作校准数据：

```json
{
  "id": "用户指定的唯一标定编号",
  "evidence_kind": "independent_reference",
  "reference_source": "参考仪器编号、校准证书、原始数据路径与日期",
  "rows": [{
    "binding": {},
    "measured_voltage_ac_v": [0.0, 0.0],
    "reference_voltage_ac_v": [0.0, 0.0],
    "measured_current_ac_a": [0.0, 0.0],
    "reference_current_ac_a": [0.0, 0.0],
    "voltage_range_v": [3.0, 4.2],
    "current_ac_range_a": [0.05, 0.35],
    "reference_uncertainty_relative": 0.005
  }]
}
```

零信号输入会被拒绝。实测填写后执行：

```bash
python3 ps/app.py calibrate-transfer --input reference_measurements.json --output config/transfer.json
```

校准绑定设备/转换器身份、前端制造文件与最终装配日志哈希、BE03 位流哈希及 ABI、ADC 时钟/参考电压、PGA/BUF/DRATE/MUX、采样槽、相位增量、方波高码、稳定与采集时长、估计器和 DC 校准。只支持已测频点，不插值或外推。复数系数幅度限制 0.5–2 仅是输入合理性检查，不能证明校准准确。最终分压改变了电压通道 RC 极点，两通道的增益和相位必须分别校准。

## 外置串联标准的 A/B 操作

标准件必须先由独立仪器测得目标频率下的**复阻抗**及不确定度，不能只填标称 10 mΩ。四线夹持位置固定，电池和标准的温度、接触状态及短时漂移需记录。

1. 停止、实测 DA0=0 和负载关断后接线：电池正极 → 标准 → F+；F− 与 S− 接电池负极，标准始终留在主放电路径。
2. A：S+ 接标准的电池侧 Kelvin 点。电流仍测 R2；得到电池端口的 `ZA`。
3. 完成 A 后，等待界面停止及数字零码确认，再实测关断。保持标准、F+/F− 和 S− 不动，只把 S+ 移到标准的负载侧 Kelvin 点。
4. B：相同频率、采样、校准、温度/SOC 条件重复，得到 `ZB`。
5. 线性与近似平稳条件下 `VB_ac = VA_ac − I_ac·Zstd`，故 `ZB−ZA ≈ Zstd`。主电流路径未改变；实测电流仍需比较，不能假定相同。

CLI 示例（`board.json` 和标准文件必须已实测填写）：

```bash
python3 ps/app.py measure --profile config/board.json --overlay release/be03_20261022/battery_eis.bit --frequency 80 --position A --pair-id cell01-std01-001 --confirm-zero-before-move --output data
# 完成停机确认并移动 S+ 后，再启动 B
python3 ps/app.py measure --profile config/board.json --overlay release/be03_20261022/battery_eis.bit --frequency 80 --position B --pair-id cell01-std01-001 --confirm-zero-before-move --output data
python3 ps/app.py ab-export --session data/A实际目录 --frequency 80 --output A.json
python3 ps/app.py ab-export --session data/B实际目录 --frequency 80 --output B.json
```

若使用传递校准，A 与 B 都附加同一个 `--calibration config/transfer.json`。面板选择 A/B 也要求启动时传入 pair-id 与移动前停机确认；每次采集仍执行硬件零码检查。`ab-export` 先从原始采样复算全部18项PL累积、结果和重复性，再导出至少两次质量合格的重复；保留所有输入文件摘要及仿真/实测来源。旧会话没有校准快照时，导出命令需额外传入原始 `--calibration` 文件。未校准但质量合格的会话可作A/B诊断，报告仍为未校准；不得把诊断转成实物准确度证明。

`standard.json` 格式：

```json
{
  "independently_measured": true,
  "reference_source": "填入独立复阻抗测量记录，不可只填标称值",
  "frequency_hz": 80.0006091594696,
  "impedance_ohm": [0.01, 0.0],
  "uncertainty_relative": 0.005
}
```

上面阻抗和不确定度仅演示格式。将 A、B、标准三个文件的对象组合为 `{"a": A对象, "b": B对象, "standard": 标准对象}` 保存为 `ab_input.json`，再运行：

```bash
python3 ps/app.py ab-check --input ab_input.json --output ab_report.json
```

标准阻抗和频率必须以实际记录为准。B位置测到电池与串联标准的总交流响应；两位置都受10mV交流扰动质量门限约束。离线构造中，约2.165Hz、10mΩ标准使B峰值约11.49mV而被拒绝，80Hz同一标准通过。不能保证一颗10mΩ标准在所有电池和频点均可用；到板后应实测幅度与线性，不为通过测试直接放宽门限。该构造不是实物标准件验证。

程序检查同一来源、pair/cell/binding/校准、停机记录、质量标记、目标频率、参考不确定度、电流变化 ≤2%、平均端电压变化 ≤10 mV，并报告复数差值和相对残差。来源缺失或混用simulation/hardware会被拒绝。2%/10 mV 为粗筛条件，不能证明电池在 A/B 之间平稳。报告不自动置 `board_validated=true`，不把标准件逼近某个常数，不覆盖原始结果。

## 不确定度与有效性

`random_uncertainty_ohm` 只来自拟合残差和两通道复幅值传播，不含全部系统误差。单窗 `valid` 表示已匹配校准且通过质量筛选；会话 summary 还检查重复性。下游必须同时检查最终 summary 和结果 reasons。重复性相对标准差 >5% 时会话置 `UNVERIFIED`、最终结果标记无效；此前单窗文件保持原样，便于审计。

尚未通过实物参考、不同幅度及时间稳定性验证前，不据单个 `valid` 字段宣称某个百分比绝对准确度。
