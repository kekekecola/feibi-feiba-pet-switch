---
name: feibi-hat-switch
description: 控制已安装的 Windows 菲比 / 菲八 F8 切换服务，执行戴帽、摘帽、切换或查询状态。
---

使用环境变量 `FEIBI_PET_SWITCH_HOME` 定位安装目录。未配置时询问安装目录，勿猜测用户路径或扫描其全部文件。

版本化安装目录有 `current.json`，通过 `.venv/Scripts/python.exe launcher.py control status` 查询；用同样入口把status替换为take-off、put-on、toggle或quit。普通源码安装目录通过 `.venv/Scripts/python.exe app/bridge_control.py` 控制。将路径作为单独参数传递。不要直接启动 player.py，CG 不是常驻宠物。

动作前先查状态。运行中且 `connection_state=ready` 时发送请求；重连时等待，勿重复启动服务。确实未运行且用户要求开启或切换时，可运行安装目录的启动脚本（当前用户计划任务 FeibiFeibaPetSwitch），再等待就绪。服务不会主动打开桌面客户端。

回执 `result=completed` 才代表完成。`cancelled` 表示未执行；`in_progress` 表示已开始，按 command_id 查询运行数据目录中的bridge-acks，不要重发 toggle。

报告切换完成前确认 `phase=idle`、`native_switch_confirmed=true`、`native_visible=true`、`cg_visible=false`、目标form及selected_id与本地映射一致。快捷键就绪还需 `hotkey_registered=true`，当前生效按键由hotkey字段读回，不假定一直是F8。版本化安装的运行数据在根目录runtime；普通安装在app/runtime。映射不存在时使用当前版本app/pet-roles.json默认值。

版本化安装通过托盘或独立计划任务FeibiFeibaPetSwitchUpdate检查并应用稳定更新，不能把“联网失败”报告为“已经最新”。查看runtime/update-status.json的installed_version、latest_version、phase和error。程序保持旧版本时不要强行覆盖运行中的源码。

失效时读取状态中的 error 和本地日志。不要调用云端选宠冒充本机切换，不改客户端程序，不把原生 MSAA 失败替换为可能抢焦点的 UIA 选择包装或指针点击。首次准备辅助窗口可能短暂激活客户端；日常切换走后台。
