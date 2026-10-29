# BE03 寄存器、数据与保护

## 数据路径

PL 100 MHz NCO → 50% 单极 DAC 门控 → PCB 负载；ADS1256 MUX01/23 交替采集 → DRDY 相位/时间戳 → 16384×128-bit 原始 RAM 与两个 9 项 64-bit 锁相累积器。PS 读取冻结记录，解 DC/sin/cos 最小二乘，复幅值采用 `cos − j·sin`，计算 `−V/I`。

另用原始时间戳独立拟合 DC、线性漂移、基波、3/5 次谐波和不重合的 50/60 Hz 分量。低信噪比、PL 与诊断拟合偏差、坏采样、饱和、阈值错误会拒绝质量标记。谐波拟合不是模拟抗混叠替代品。

## GPIO 地址与控制

| IP | 地址 | 通道 |
|---|---:|---|
| waveshare_config | 0x81260000 | offset0 参数负载，offset8 控制 |
| left_result | 0x81270000 | offset0/8 左 64-bit 或原始 tick/phase |
| right_result | 0x81280000 | offset0/8 右 64-bit 或原始 code/channel |
| waveshare_status | 0x81290000 | offset0 状态 |

控制 bit31 RUN、30 DRIVE、29 INIT、28 心跳翻转、27 命令翻转、26:23 命令号、22 选择统计页、21:16 页号、13:0 原始索引。命令 1 相位增量、2 稳定 ticks、3 采集 ticks、4 欠压码、5 过压码、6 过流码、7 DAC 高码。只在停止且零码确认后写入。

状态高 16 位固定 BE03；15:8 序列号，7 命令确认，6 统计无效，5 原始溢出，4 零码帧确认，3 故障，2 DONE，1 ACTIVE，0 ADC READY。驱动先验 ABI，再写控制，拒绝旧 BE01/BE02/574A。地址及数据布局沿用，版本改变隔离长窗口和扩展原始RAM；最终R100/1.249装配保持不变。

统计页 0–8 为 `n,sum(x),sum(s),sum(c),sum(s²),sum(c²),sum(sc),sum(xs),sum(xc)`；s/c 为 14-bit LUT、幅度 8191。左/右分别电压/电流，以低32位+高32位拼接有符号64位。页9为 count/tick/fault/sequence；页10为 ADC 状态、MUX、ADCON、DRATE 的拼接值/OFC/FSC/槽长；页11为相位增量/稳定ticks/采集ticks/高码。页12为RAM容量16384、最小DDS增量86、最大DDS增量4295、最大采集ticks400000000。

原始记录四个32-bit字段：DRDY tick、NCO phase、有符号24-bit符号扩展码、channel（0电压/1电流）。timebase 100 MHz，每次 RUN 从零计时，phase随同计数。驱动在 DONE+ZERO 后读，前后检查序列/状态，保留寄存器读回；分析端再核对实际配置和 `phase == tick×increment mod 2^32`。

## 保护范围

- PS 以 `code_volts=5/8388607`、`Vdc=code_dc×code_volts×1.249×voltage_dc_scale`、`Idc=code_dc×code_volts/R2×current_dc_scale` 换算 DC 读数，并将电池限值反算成 PL 阈值。R2 默认 0.100 Ω；以独立实测值和 DC 校准为准。AC 复幅值先按名义1.249和实际R2换算，再分别乘独立标定的Cv/Ci；不重复套用DC scale。
- PL 最大允许电压阈值 8,388,606 码，电流阈值 120,000 码，保留 ADC 饱和检查。示例 3.0–4.2 V/0.45 A 对应 4,029,755/5,641,657/75,497 码。5 V/0.55 A 的名义上阈值为 6,716,259/92,275 码。PS 对不可表示的阈值报错，不自动截断。0.55 A 是本版配置上限，实际限值仍由电池和装配确认决定。
- 反向电流固定阈值为 −168 码，名义约 −1.00 mA；DC 比例及 R2 偏差会改变其实际值，须在实物验收中核实。旧 BE01 的电压码上限不能覆盖最终装配的正常 3.7 V 输入，因此本版必须与新位流成对使用。
- 初始化先发 DA0 零码；ADC 复位、SELFCAL 与配置读回不通过不进入 READY。
- PL 有限时窗自动结束；采集期间心跳最大间隔 0.5 s。PS 正常每约80 ms 翻转，线程异常或进程被停止时仍由 PL 超时关断。
- ADC 转换等待上限10 ms，初始化等待上限0.5 s。欠压、过压、过流、反向电流异常、ADC饱和、原始 RAM 满均触发停止。SPI 按字节边界退出，再发送零码。
- 故障位：0 ADC超时，1配置错误，2状态机异常，3心跳超时，4ADC饱和，5RAM满，6电压范围，7电流范围，8配置范围，9用户取消。
- 采集最长4秒，最多约12208条（DAC仲裁可能降低吞吐），RAM容量16384；频率候选2–100Hz。统计排空后置DONE；PS等待ZERO再读取。
- 零码是数字确认，DAC、MOS、接线、供电或器件失效不能由此保证关断。特别是 PL 停钟/重配置不能靠 PL 看门狗保护，因此加载位流、异常失去控制及移动接线前应断开电池并实测。

## 保存格式与健康模型接口

每个会话使用 UTC 时间+UUID 创建新目录，不覆盖已有数据：

| 文件 | 内容 |
|---|---|
| session.json | schema、source、设备profile、设置、cell_id、温度/SOC/循环/容量等上下文 |
| adc_selfcal.json | 实际配置和ADC内部校准寄存器 |
| NNN_raw.csv | sample_index, drdy_tick_100MHz, phase_u32, adc_signed24, channel |
| NNN_capture.json | raw文件名、统计矩、binding、序列、故障、实际寄存器、采集位置 |
| NNN_result.json | 单窗复电压/电流/阻抗、幅相、诊断、校准ID、有效性、理由 |
| repeatability_频率.json | 多窗实/虚部离散度与相对标准差 |
| summary.json | 最终状态、全部结果、失败原因、AI状态/实际特征/模型哈希及阶段计时 |
| measurement_plan.json | 每个频点的真实窗口、重复数、窗口下界与会话预算 |
| calibration_used.json | 本次校准的完整文档、SHA-256摘要及范围说明；无校准时不生成 |

复数统一 `[real, imaginary]`，单位 V/A/Ω，频率为实际NCO频率，同时保留请求频率。可通过 `--metadata context.json` 提供 `temperature_c`、`soc_fraction`、`cycle_count`、`capacity_ah`、`port_kind` 和实验说明；未输入则null。使用稳压端口时填写 `port_kind=regulated_source`，不得当作电池标签。`source` 区分 simulation/hardware。

`audit-session`离线复算原始数据、18项累积及会话结果；`export-candidate`只把通过复算的正常电池测量整理为未标注候选。容量/SOH/RUL标签不会由手填元数据或现有模型输出自动生成。详见 `be03_ps_validation_20261022.md`。

PS已接入冻结的公开数据研究模型：先检查完整双频、独立重复、会话状态、质量和校准，再按每点复数均值生成全Re后全Im的输入（Ω）。soc_fraction乘100转换为soc_percent，不重复施加电压/分流系数。hardware强制使用battery_eis_rev_a域，simulation强制使用simulation域，用户元数据不能冒充训练数据。两者均尚未通过模型域验证，soh/rul保持null。独立replay-model命令可输出明确标记的公开数据研究SOH；valid_for_board=false。模型核本轮运行在PS，独立PL推理核尚未接入。
