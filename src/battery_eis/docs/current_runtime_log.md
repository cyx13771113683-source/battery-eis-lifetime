# 当前运行日志（2026-10-30）

用户要求后续运行日志每次重启清空。自v4起，正常启动程序或板卡开机自启都会以覆盖模式打开板端 `/home/xilinx/battery_eis_deploy_20261026/runtime.current.log`，仅包含本次程序启动后的stdout/stderr；`--verify-only`不改日志。日志同时镜像到systemd journal，保持安装器首帧检查可用。

后续排查优先读取 `runtime.current.log`。若需journal，只按服务当前 `InvocationID`过滤；`journalctl -b`仅过滤板卡开机，并不能区分同一次开机内多次程序重启，不能把此前启动的ERROR当成本次故障。

这项清空只针对当前运行日志。测量会话、原始采样、SOH历史、B01—B20批次进度、校准与配置都继续保留。系统历史journal作为追溯证据保留，不执行全局日志删除。
