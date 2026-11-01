# 比赛提交对照

依据《全国大学生嵌入式芯片与系统设计竞赛2026选拔指南（AMD）》第3.3.1—3.3.5节，PDF第22—25页（印刷页19—22）。

| 要求 | 提交内容 |
|---|---|
| 英文目录/文件名、跨平台复现 | 根目录README与`sim/package_check.json`；README.md和LICENSE沿用标准名称 |
| 设计源代码、接口与工程 | `src/battery_eis/`；`build/base.bd`、`build/ip/`、`build/constraints/` |
| 仿真、软件验证与结果 | `sim/`；源码tests/sim及RTL运行脚本 |
| 综合实现、资源、时序 | `build/reports/`；`build/build.ps1`可复现入口 |
| 上板产物、运行脚本、实物输出 | `board/be03/`、`board/application/`、`board/README.md`、`data/hardware/normal/`、`data/boot_20261030/`、`data/batch_20/` |
| 设计原理、划分、优化对比、复现 | `report/technical_report.docx`、`report/design_report.md`及根目录README |
| 技能包的来源、步骤、效果与边界 | `skill/README.md` |
| 大模型提示词、响应及自我纠错 | `report/collaboration.md` |
| 许可证与第三方来源 | MIT `LICENSE`、`report/third_party.md`及模型元数据 |

本仓库不附带参赛账号、参赛登记、答辩视频或官方上传操作；源代码包不替代组委会另外要求的报名材料。当前版本没有重新运行完整综合，冻结硬件报告与本次软件测试分开标注。模型容量准确率不由本次重启验证推导。

指南3.3.5.3要求设计报告，3.3.5.4推荐report/目录；未限定必须使用Word。本仓库同时提供负责人完成的Word报告与可浏览的源码导航摘要。文件名依规则使用英文。
