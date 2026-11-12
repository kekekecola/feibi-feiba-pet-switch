# 菲比啾比 / 菲八啾比 Codex 桌宠 · F8 戴帽与摘帽切换

在 Windows 版 Codex 桌面客户端的两只原生宠物之间，通过 **F8** 播放摘帽 / 戴帽 CG 后切换形态。包含菲比啾比、带番茄发卡的菲八啾比、透明过渡动画及全部切换源码。

**Feibi Jiubi / Feiba Jiubi — native Codex desktop pets with F8 hat-on/off transitions.**

An unofficial Windows companion project featuring **Feibi Jiubi (菲比啾比)** and **Feiba Jiubi (菲八啾比)**. Press F8 to switch between the two native pets through transparent hat-removal or hat-wearing animations. Includes both pet sprite sheets, the transition player, background switching bridge, installer, and optional chat-control skill. Animation size follows the native pet size and display DPI. Requires Python 3.11+ and a compatible Codex desktop client; not a standalone EXE. See the installation steps, safeguards, limitations, and license below before use.

Version 1.2 adds a native Windows tray icon, local diagnostics, configurable live hotkeys, reduced redundant UI traversal, and opt-in verified stable-release updates with version staging and startup rollback.

| 摘帽 | 戴帽 |
| --- | --- |
| ![摘帽预览](media/hat-take-off.gif) | ![戴帽预览](media/hat-put-on.gif) |

GIF 是动画预览；实际过渡使用 PNG 逐像素透明叠层。正式常驻的是客户端原生宠物。

## 下载和安装

### v1.2.0 托盘、快捷键、提速与自动更新

建议下载最新补丁版 **v1.2.3**：修复Windows换行差异造成的多余依赖重装，以及隐藏进程链路中的命令行状态输出，并在原生选择前修复一次失效的后台按钮，自动化测试增至35项。

- 系统托盘显示准备、切换、重连或异常状态。右键托盘可切换形态、打开本地诊断、设置快捷键、检查更新或退出。托盘可能被 Windows 放在折叠区。
- 默认 F8，可在托盘设置中改成其他功能键或 `Ctrl+Alt+H` 等组合键，无须重启。注册失败时保留原绑定；重启时尝试恢复上次可用按键。F12 与 Windows 键组合不支持。
- 去掉普通切换中重复的 MSAA 控件扫描，单次扫描只加载一次角色配置，并合并原生图像查找中的重复遍历；隔离进程与超时恢复保持。本机两次旧版样本为4.01/4.09秒，新版为3.66/3.33秒，平均约快14%，不是所有电脑的性能保证。
- 可选自动更新：每30分钟及登录时检查该仓库的稳定发行版，核对 GitHub 提供的 SHA-256 和素材清单，先暂存、准备依赖，再停止旧服务、切换并检查启动健康状态。联网、下载或准备依赖失败时不停止旧版；新服务启动失败时尝试恢复上一版。

**开启自动更新：**先完成下面的普通安装并退出切换服务，再双击 `开启自动更新.cmd`。它会在 `%LOCALAPPDATA%/FeibiFeibaPetSwitch` 建立独立环境、版本目录和共享运行配置，注册 `FeibiFeibaPetSwitch` 与 `FeibiFeibaPetSwitchUpdate` 两个当前用户任务，不会自动打开客户端。原生宠物文件和客户端大小设置保留。版本化安装成功后，以托盘菜单管理服务；自定义安装路径可运行 `python manager.py enable --home "你的目录"`。

检查结果与失败原因可在托盘“一键诊断”查看。自动更新接受稳定版，不自动降级；它依赖本机登录、联网、GitHub可用及任务权限，不能承诺始终即时更新。网络恢复后的后续检查会重试。自动更新更新程序与随包CG，不调用云端接口改写现有云端宠物图集；涉及宠物本体素材变化时须按对应发行说明刷新。上次工作版本和切换记录保留；不保证断电或系统同时终止所有相关进程时立即恢复。

版本化目录中的命令行入口：`.venv\Scripts\python.exe launcher.py control status`，还支持 `control toggle`、`diagnose`、`settings`、`update`（手动更新建议从托盘启动独立更新任务）。`launcher.py disable-updates` 可关闭更新任务；卸载脚本会先移除更新任务，再停止主服务，保留数据与版本文件。

### v1.1.0 安装与维护优化

- 安装出错时回滚本次管理的两只宠物文件、本地角色配置和计划任务；升级前停止旧服务，回滚后按原状态尝试启动。旧宠物备份继续保留。回滚不覆盖 Python 虚拟环境和下载缓存，也不保证断电或强制终止安装器后的自动回滚。
- 卸载与修改角色配置不再依赖素材校验。素材或运行依赖损坏时，卸载仍会尝试停止并移除计划任务；重复卸载允许任务已不存在，宠物和备份保留。
- F8 主日志每512KB轮转，保留两个归档；隔离工作进程使用独立日志，避免多进程同时轮转同一文件。服务空闲时每小时清理超过7天的历史回执、决策记录和工作进程日志，保留待处理/活动请求、当前配置及窗口恢复记录。

详见 [更新记录](CHANGELOG.md)。

从 [Releases](https://github.com/kekekecola/feibi-feiba-pet-switch/releases) 下载 ZIP 并解压到长期保留、当前用户可写的文件夹。也可以下载仓库源码。

需要 Windows、安装了 Tcl/Tk 的 **Python 3.11+**、支持原生 Pets 和本地 pet.json 的 Codex 桌面客户端。首次安装需要联网从 PyPI 安装 Python 依赖。当前仅适配安装包路径含 `OpenAI.Codex`、主进程为 `ChatGPT.exe` 的客户端；不适用于网页 ChatGPT 或其他桌宠软件。

1. 双击 `安装.cmd`，或在该目录运行 `py -3 install.py install`。
2. 在客户端的设置中找到 Pets / 迷你界面和智能伙伴，刷新或重启客户端后选择 **菲比啾比** 或 **菲八啾比**，并显示原生宠物。
3. 等待后台准备完成，按 **F8**。菲比 → 摘帽 CG → 菲八；再次按下则戴帽返回。
4. 宠物大小继续在客户端设置里调整，CG 跟随实际原生尺寸与屏幕缩放。

安装会创建本项目的 `.venv`、将两只宠物复制到 `$CODEX_HOME/pets`（未设置时为 `~/.codex/pets`），并注册当前用户的登录任务 `FeibiFeibaPetSwitch`。同名旧宠物会先复制到 `pet-backups` 备份。计划任务只启动切换服务，不启动客户端。安装后不要移动目录；移动后需要重新安装以更新任务路径。

`启动.cmd` 启动服务，`退出.cmd` 停止服务。退出后登录任务仍保留；运行 `py -3 install.py uninstall` 或 `卸载.cmd` 可移除登录任务，宠物、备份和源码保留。

## 保障了什么

下表是实现的保护和本机实测结果，**不是所有系统、未来客户端版本均无故障的承诺**。详细证据和未覆盖场景见 [验证说明](docs/VALIDATION.md)。

| 范围 | 已实现 / 已实测 |
| --- | --- |
| 只常驻一只原生宠物 | 正常过渡先隐藏原生窗口，播放临时 CG，再撤 CG 并恢复原生；异常使用带 PID 和窗口标记的恢复记录，避免恢复错误窗口。 |
| 动画尺寸 | 根据原生图像控件实际物理尺寸、DPI、客户端大小设置适配，CG 中持续检查大小，按脚底位置对齐。 |
| 焦点与当前页面 | 准备完成后的日常切换调用隐藏设置窗口的原生 MSAA 动作。本机双向切换采样中，输入焦点与主页面保持不变。 |
| 调用卡住 | 控件发现和选择在隔离子进程执行，切换上限22秒、初始化上限35秒；超时结束本工具的工作进程，撤 CG 后尝试恢复原生，主 F8 服务继续运行。 |
| 请求取消 | 过期或取消的未开始命令不再执行；已开始的命令返回 `in_progress`，避免误判后重复切换。 |
| 重连 | 原生辅助窗口消失后尝试重建；重连期间保留一个请求，重复过渡请求忽略。独立登录任务不依附于客户端父进程。 |
| 名称 / ID 变化 | 通过图像指纹、完整描述和原生图像名称重新确认角色；重复匹配停止操作，不猜测目标。账户 ID 仅保存在本机运行配置。 |
| 菜单语言 | 从正在运行的客户端语言包按语义键读取标签；自带中英文回退。读取了多语言标签不等于在每种语言中逐一实测。 |
| 本地数据 | 不读取账户口令或 API key，不需要云端宠物控制接口；运行记录只写入本地 `app/runtime`，源码仓库不包含个人运行记录。 |

## 没有什么 / 不保证什么

- **没有原生宠物右键“摘帽 / 戴帽”菜单。** 可通过可配置的全局快捷键、托盘菜单、命令行和可选 skill 触发。
- 没有修改、注入或补丁化客户端程序；这是依赖 UI Automation / MSAA 的非官方外部辅助工具，**不是 OpenAI 官方受支持的切换 API**。
- 没有一键 EXE，也没有捆绑 Python 解释器；第一次安装需 Python 和联网。尚未在全新电脑完整验证安装流程。
- 不支持 macOS、Linux、浏览器版或移动版；F8 被其他软件占用时不能注册。某些笔记本需 `Fn+F8`。
- 不保证首次启动 / 重建辅助窗口完全不激活客户端：初始化可能短暂影响焦点。普通 F8 切换已单独验证不抢焦点。
- 不保证客户端未来更新、界面结构变化、权限隔离、远程桌面、休眠唤醒、多屏跨 DPI 拖动或用户同时手动换宠时仍完全一致。后台窗口关闭重建已测，未在本次发布流程中再次关闭整个聊天客户端实测。
- 不保证断电或所有相关进程同时被强制终止时立即恢复窗口；日志和身份记录会用于后续启动恢复。卡死模拟和单个切换进程退出恢复已测。
- 两只宠物不是每帧外轮廓完全相同的模型；已校准大小、眼睛与脚底基准，但姿势、帽子与头发轮廓本来就不同。GIF 预览也不代表所有屏幕条件下的最终效果。
- 不把第三方角色、商标或原始素材的权利转授给使用者；开源代码授权范围见下文。

## 已有云端宠物 / 自定义映射

若已有这两只同名云端宠物，**不要再安装同名本地副本**，否则后台会因重复按钮拒绝切换：

```powershell
py -3 install.py install --skip-pets --feibi-id pet_YOUR_FEIBI_ID --feiba-id pet_YOUR_FEIBA_ID
```

ID 可在你自己的 `~/.codex/config.toml` 的 `[desktop]` 中读取 `selected-avatar-id`：分别选中两只宠物后记录，不需要向任何人发送完整配置。默认本地 ID 为 `custom:feibi-jiubi` 和 `custom:feiba-jiubi`。

运行映射保存在 `app/runtime/pet-roles.json`，默认模板为 `app/pet-roles.json`。也可运行 `install.py configure --feibi-id ... --feiba-id ...` 后退出并启动服务。名称、描述和图像同时改变时需更新映射中的名称和描述。请勿把自己的 runtime 文件提交到仓库。

## 命令行与 Skill

以下是普通源码安装的命令；版本化安装使用上文的launcher.py入口。

```powershell
.venv\Scripts\python.exe app\bridge_control.py status
.venv\Scripts\python.exe app\bridge_control.py take-off
.venv\Scripts\python.exe app\bridge_control.py put-on
.venv\Scripts\python.exe app\bridge_control.py toggle
```

`result=completed` 才代表完成；失败后先查询状态。`in_progress` 表示已开始，不要重复发送 toggle。健康状态应为 `phase=idle`、`connection_state=ready`、`hotkey_registered=true`；完成切换还应有 `native_switch_confirmed=true`、`native_visible=true` 和 `cg_visible=false`。

可把 `skills/feibi-hat-switch` 复制到自己的 Codex skills 目录，并将环境变量 `FEIBI_PET_SWITCH_HOME` 设置为本项目解压目录，即可用 `$feibi-hat-switch 摘帽 / 戴帽 / 查询状态`。可选 skill 不会随安装器自动装入你的个人技能目录。

## 开发和验证

```powershell
py -3 install.py verify
py -3 install.py install --dry-run
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

CI 运行代码、资源、请求协议和隔离超时测试；CI **不测试真实桌面或原生客户端**。依赖版本见 `requirements.txt`。窗口 / 进程识别、恢复日志及透明播放器实现均在 `app/`。

## 许可与来源

本项目新增代码与文档使用 [MIT License](LICENSE)，允许下载、使用、修改与再分发，保留许可和归属声明。菲比图集及上游相关部分来源于 [KanadeK/feibi-jiubi-codex-pet](https://github.com/KanadeK/feibi-jiubi-codex-pet)，保留 [上游 MIT 许可](LICENSE.upstream) 和 [上游声明](NOTICE.upstream.md)。菲八和过渡素材为本项目制作、调整的派生素材。

这是非官方同人项目，与 OpenAI、库洛游戏及其他权利人无隶属或认可关系。MIT 不授予第三方角色、名称、设计或商标权利。详细素材归属见 [NOTICE](NOTICE.md)。
