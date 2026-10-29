# 比赛提交对照

依据2026比赛选拔指南第3.3.5节。

| 要求 | 本仓库材料 |
|---|---|
| 纯英文仓库、目录及文件名 | battery-eis-lifetime；文件路径检查记录见sim/package_check.json |
| 源码与工程 | src/battery_eis；build/base.bd、build/ip、build/constraints |
| 仿真验证 | sim/；src/battery_eis/tests、sim、scripts |
| 综合实现和性能 | build/reports |
| 上板工程与运行 | board/be03；board/README.md |
| 实物验证 | report/validation.md，负责人确认已通过；data/hardware/normal保存2026-10-28原始采样与报告 |
| 设计报告 | report/design_report.md |
| 技能包 | skill/README.md |
| 大模型协作记录 | report/collaboration.md（本次整理摘录） |
| 开源协议 | 自有代码采用MIT；第三方IP与数据来源见third_party.md |

仓库收录与本项目相关的现有模型和例子。公开数据集的完整来源及重训练说明沿用模型JSON元数据；本提交不扩充训练数据。
