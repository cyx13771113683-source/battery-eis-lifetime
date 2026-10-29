# Vivado 构建

版本 2025.2，器件 xc7z020clg400-1。运行方法见根目录 README。构建从随仓库提供的 base.bd、RTL、XDC 和 PYNQ IP 创建独立工程。

第三方依赖为 PYNQ 的板级 HDMI/GPIO 基础设施；原许可证位于 licenses/，各 IP 文件内版权声明保留。使用的自定义IP：

- xilinx.com:user:audio_codec_ctrl:1.0
- digilentinc.com:ip:axi_dynclk:1.0
- xilinx.com:user:color_swap:1.1
- digilentinc.com:ip:dvi2rgb:1.7
- digilentinc.com:ip:rgb2dvi:1.2

reports/ 为冻结 BE03 构建结果；构建生成新输出，不覆盖该证据。
