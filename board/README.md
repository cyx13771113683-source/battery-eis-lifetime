# 板端运行

板卡：TUL PYNQ-Z2 / xc7z020clg400-1。使用 `be03/battery_eis.bit` 及同名 HWH；XSA 用于硬件平台交接。板端需 PYNQ、NumPy、Pillow，并具备 HDMI/触摸环境。

1. 按 `src/battery_eis/docs/final_assembly_log_20261021.md` 核对装配与接线。J1为F+、S+、S−、F−；AD3/F−不得另短接AGND。
2. 在 `data/profile.example.json` 的副本中填写实测比例、阈值和已确认的硬件信息，使用匹配的复数传递校准。模板保持确认项为false。
3. 将完整仓库复制到板端，断开电池后加载匹配固件；先核对零驱动和保护，再依本机已验证流程接入电池。
4. 从仓库根目录运行：

```sh
sudo python3 -B src/battery_eis/ps/app.py panel --profile /path/to/verified_profile.json --overlay board/be03/battery_eis.bit --calibration /path/to/calibration.json --output outputs/measurement
```

本提交不包含设备凭据、私人校准或网络配置。实物验证状态见 `report/validation.md`。
