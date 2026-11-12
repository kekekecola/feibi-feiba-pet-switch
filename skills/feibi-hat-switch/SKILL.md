---
name: feibi-hat-switch
description: 控制已安装的 Windows 菲比 / 菲八 F8 切换服务，执行戴帽、摘帽、切换或查询状态。
---

使用环境变量 `FEIBI_PET_SWITCH_HOME` 定位安装目录。未配置时询问安装目录，勿猜测用户路径或扫描其全部文件。

目录下 `.venv/Scripts/python.exe` 是解释器，`app/bridge_control.py` 是控制入口。将路径作为单独参数传递。只读查询 `status`，用 `take-off` 摘帽、`put-on` 戴帽、`toggle` 切换、`quit` 退出。不要直接启动 player.py，CG 不是常驻宠物。

动作前先查状态。运行中且 `connection_state=ready` 时发送请求；重连时等待，勿重复启动服务。确实未运行且用户要求开启或切换时，可运行安装目录的启动脚本（当前用户计划任务 FeibiFeibaPetSwitch），再等待就绪。服务不会主动打开桌面客户端。

回执 `result=completed` 才代表完成。`cancelled` 表示未执行；`in_progress` 表示已开始，按 command_id 查询 `app/runtime/bridge-acks`，不要重发 toggle。

报告切换完成前确认 `phase=idle`、`native_switch_confirmed=true`、`native_visible=true`、`cg_visible=false`、目标form及selected_id与本地映射一致。F8 就绪还需 `hotkey_registered=true`。映射位于 `app/runtime/pet-roles.json`，无该文件时使用 `app/pet-roles.json` 默认值。

失效时读取状态中的 error 和本地日志。不要调用云端选宠冒充本机切换，不改客户端程序，不把原生 MSAA 失败替换为可能抢焦点的 UIA 选择包装或指针点击。首次准备辅助窗口可能短暂激活客户端；日常切换走后台。
