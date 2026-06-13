# wxAutoControl 架构说明

`wxAutoControl` 是一个面向 Windows 桌面微信客户端的自动化控制项目，当前聚焦两类能力：发送消息与通过手机号添加好友。项目采用“控制器编排 + 视觉自动化驱动”的结构，优先尝试通过 `uiautomation` 识别窗口与控件树；当 mmui 业务树不可用时，退回到基于截图、OCR、相对坐标、键鼠输入的视觉链路。

当前仓库形态更接近一个独立自动化能力包，外部调用方通过包函数或测试脚本触发操作。核心实现集中在 `wxautocontrol/`，文档与知识库配套目录位于 `.Knowledge/`，运行期日志与视觉缓存分别写入 `wxauto_logs/` 与 `.task/runtime/`。

## 核心概念

| 概念 | 说明 |
|------|------|
| `WeixinController` | 控制器主入口，负责窗口检测、参数校验、节流控制、业务流程编排和结果组装。 |
| `VisualWeixinWindow` | 视觉自动化基础窗口对象，封装截图、OCR、点击、输入、窗口激活、尺寸标准化等底层能力。 |
| `VisualAddFriendWindow` | 添加好友资料页窗口对象，负责结果状态判断、按钮定位与申请入口打开。 |
| `VisualFriendRequestWindow` | “申请添加好友”弹窗对象，负责表单填写、标签选择、权限设置和确认提交。 |
| `WindowDiagnostic` | 微信窗口识别结果，描述当前客户端形态，如 `mmui`、`qt-webview-host`、`qt-shell-only`。 |
| `ControlResponse` | 对外统一返回结构，包含顶层 `status`、`code`、`message`、`data`。 |
| `ControlConfig` | 运行常量集合，定义超时、节流间隔、主窗口目标尺寸、日志开关等。 |
| 视觉缓存 | `.task/runtime/visual-weixin-cache.json` 中保存的坐标缓存，用于降低重复 OCR 成本。 |
| `uilock` | UI 串行化锁，保证同一时刻只执行一个桌面自动化流程。 |
| `entry_mode` | 添加好友入口模式，当前支持 `global_search` 与 `add_menu` 两种路径。 |
| 常驻 runtime | `tests/run_runtime.py` 提供的常驻控制台模式，允许一次预热后连续执行发送消息、连续发送、添加好友和刷新控制器。 |

## 状态与流转

- **窗口识别阶段**：先检测是否存在微信主窗口，并区分 `mmui`、`qt-webview-host`、`qt-shell-only`、`unknown` 等客户端形态。
- **发送消息阶段**：定位目标会话，进入聊天窗口，写入消息并提交，最终返回 `sent`、`session_not_found` 或 `ui_error`。
- **添加好友阶段**：从指定入口模式进入搜索或资料页，识别结果状态，再决定是否进入申请表单。
- **搜索结果状态**：当前流程显式区分 `not_found`、`already_friend`、`pending_verification`、`unknown` 以及可继续申请的资料页状态。
- **提交后状态**：提交申请后继续识别 `applied`、`rate_limited`、`permission_required`、`request_form`、`send_form` 等后置状态，并由控制器映射为成功或失败响应。

## 业务规则

- 项目运行环境固定为 Windows 桌面，且要求微信客户端已启动、窗口可见、允许前台交互。
- UI 自动化必须串行执行。所有公开控制方法通过 `uilock` 包装，避免多个流程争抢鼠标、键盘、剪贴板和前台窗口。
- 添加好友操作存在调用节流。`ControlConfig.ADD_FRIEND_INTERVAL` 用于限制两次添加请求的最小时间间隔。
- 添加好友入口模式由参数控制，当前仅支持 `global_search` 与 `add_menu`；其他值直接返回失败。
- 视觉定位采用“缓存优先，失败回退 OCR”的策略。缓存点若超出预期区域或无法推进页面状态，会被清理并重新识别。
- 发送消息搜索结果只接受 `联系人`、`群聊` 分组中的真实命中项；顶部普通搜索建议、`聊天记录`、`公众号`、`小程序`、`服务号`、`订阅号` 等区域不得作为消息发送目标。
- 当联系人命中缓存已建立时，发送消息优先直接按 `Enter` 打开会话；若缓存未能推进到聊天页，则自动清缓存并回退 OCR。
- 已缓存联系人走极速搜索路径：搜索框写入优先用缓存坐标，聊天输入框也优先复用缓存，减少重复 OCR。
- 项目不假设微信客户端只有一种 UI 结构。窗口检测先尝试 mmui 业务树，再兼容 Qt/WebView 外壳场景。
- 对外调用不直接暴露底层异常，而是统一转换为 `ControlResponse`，便于脚本和上层系统消费。
- 当前配置以内嵌常量形式维护，尚未建立独立配置文件或环境变量体系。

## 关键流程

1. **发送消息流程**
   入口：`wxautocontrol.send_message.send_message(...)` 或 `python tests/run_send_message.py`
   步骤：初始化 `WeixinController`，检测微信窗口，构造视觉窗口对象，进入目标会话并发送消息，最后组装响应。
   结果：返回发送成功、会话不存在或 UI 执行失败。

1. **连续发送消息流程**
   入口：`wxautocontrol.send_message.send_message_continue(...)`、`WeixinController.send_msg_continue(...)` 或常驻 runtime 中的 `sendc`
   步骤：复用最近一次成功发送的会话状态，直接在当前聊天输入框发送消息，不再重新搜索目标会话。
   结果：适用于“第一次已进入某会话，后续马上继续发多条消息”的连续发送场景。

2. **添加好友流程**
   入口：`wxautocontrol.add_friend.add_friend(...)` 或 `python tests/run_add_friend.py`
   步骤：校验手机号和入口模式，等待节流窗口，打开“添加朋友”界面，识别搜索结果状态，必要时进入申请表单，填写验证消息/备注/标签/权限并提交。
   结果：返回申请已发送、未找到用户、已是好友、频率受限、权限限制或 UI 失败。

3. **加号菜单添加流程**
   入口：`entry_mode=add_menu`
   步骤：在主窗口点击搜索框旁加号按钮，打开弹出菜单，进入“添加朋友”，输入手机号，点击“搜索”，再转入资料页或行内结果页继续处理。
   结果：与标准添加好友流程共用后续状态判断与申请表单提交逻辑。

4. **全局搜索添加流程**
   入口：`entry_mode=global_search`
   步骤：从主窗口搜索区输入手机号，通过搜索结果进入资料页，再执行资料页状态判断和添加申请。
   结果：适用于已有主搜索入口的标准路径。

## 接口 / API / 页面

### 包级接口

- `wxautocontrol.add_friend.add_friend(...)`
  请求：手机号、验证消息、备注、标签、朋友权限、昵称、入口模式。
  返回：`ControlResponse`，顶层使用 `status` 与 `code`，其中 `data` 包含手机号、业务状态值和入口模式等信息。

- `wxautocontrol.send_message.send_message(...)`
  请求：目标会话名、消息内容、是否精确匹配、是否发送后清空、昵称。
  返回：`ControlResponse`，顶层使用 `status` 与 `code`，其中 `data` 包含会话名和业务状态值。

- `wxautocontrol.send_message.send_message_continue(...)`
  请求：消息内容、可选目标会话名、是否发送后清空、昵称。
  返回：`ControlResponse`，用于复用当前会话连续发送，不再重新搜索。

### 脚本入口

- `tests/run_add_friend.py`
  负责解析命令行参数、打开调试日志、打印执行参数，并调用 `add_friend(...)`。

- `tests/run_send_message.py`
  负责解析命令行参数、打开调试日志、打印执行参数，并调用 `send_message(...)`。

- `tests/run_runtime.py`
  负责启动常驻控制台并预热 OCR / 控制器，支持 `warmup`、`refresh`、`send`、`sendc`、`add` 等连续命令，适合降低每次独立脚本冷启动成本。

### 关键页面与弹层

- 微信主窗口：作为所有流程的起点，承载搜索区、加号菜单和会话列表。
- 添加朋友资料页：承载搜索结果状态识别、资料页按钮定位和好友状态判断。
- 添加朋友搜索窗口：用于 `add_menu` 模式下输入手机号并执行搜索。
- 申请添加好友弹窗：承载验证信息、备注、标签、权限和最终确认按钮。

## 配置 / 数据 / 错误

### 配置

- `ENABLE_FILE_LOGGER`：是否启用文件日志。
- `ADD_FRIEND_TIMEOUT`：添加好友相关窗口查找与状态判断超时。
- `ADD_FRIEND_INTERVAL`：两次添加好友操作之间的最小等待时间。
- `FIXED_MAIN_WINDOW_WIDTH` / `FIXED_MAIN_WINDOW_HEIGHT`：主窗口视觉标准化尺寸。

### 运行数据

- `_last_add_friend_ts`：最近一次添加好友执行时间。
- `_add_friend_state_lock`：添加好友节流状态锁。
- `_last_message_target`：最近一次成功发送消息的会话名。
- `_message_state_lock`：最近消息会话状态锁。
- 视觉缓存文件：`.task/runtime/visual-weixin-cache.json`，按窗口尺寸和共享比例保存定位点。

### 错误与结果分类

- 发送消息：`sent`、`session_not_found`、`ui_error`
- 添加好友：`applied`、`not_found`、`already_friend`、`rate_limited`、`permission_required`、`ui_error`
- 顶层返回状态：
  - 成功固定为 `status=success`
  - 失败固定为 `status=error`
- 添加好友返回固定 `code`：
  - 成功对应 `ADD_FRIEND_SUCCESS`
  - 测试停在提交前对应 `ADD_FRIEND_NOT_SUBMITTED`
  - `already_friend` 对应 `ADD_FRIEND_ALREADY_ADDED`
  - 其他失败状态统一对应 `ADD_FRIEND_FAILED`
- 添加好友成功判定采用宽松策略：申请表单的确认点击已成功发出后，除非识别到明确失败状态，否则按 `applied` 返回
- 添加好友测试支持 `--not-submit`：表单填写完成后直接返回，不点击“确定”
- `add_menu` 搜索结果页状态识别已放宽：除固定按钮文案外，还会按资料页特征组合做兜底判断，并在落入 `unknown` 时输出 OCR 摘要日志
- `add_menu` 搜索后不会再把纯搜索表单误判为结果页；若点击“搜索”后未进入可识别结果，会补一次回车再校验
- `add_menu` 搜索成功不能只依赖“添加朋友”窗口标题，必须确认已离开纯搜索表单，或已经进入可识别结果页
- 发送消息搜索只认“联系人”与“群聊”分组中的命中结果；顶部普通搜索建议以及仅出现在“聊天记录”等分组里的同名词，不作为可发送目标
- 联系人搜索命中成功后会建立缓存；后续同联系人优先按 `Enter` 进入会话，失败再回退 OCR
- 若当前已停留在目标聊天会话，可直接走连续发送能力，不再走搜索链路

这些状态值最终都会通过 `ControlResponse` 转换为“顶层 `status` + `code` + `message` + `data`”的统一结构。

## 实现位置与对接方式

- **实现位置**
  - 主业务包：`wxautocontrol/`
  - 通用能力：`wxautocontrol/common/`
  - 调试脚本：`tests/`
  - 运行日志：`wxauto_logs/`
  - 视觉缓存：`.task/runtime/visual-weixin-cache.json`

- **对接方式**
  - Python 调用方可直接引入 `wxautocontrol.add_friend` 或 `wxautocontrol.send_message`。
  - 若业务侧需要连续向同一会话发送多条消息，可优先调用 `send_message_continue(...)` 或复用 `WeixinController.send_msg_continue(...)`。
  - 本地验证可使用 `tests/run_add_friend.py` 与 `tests/run_send_message.py`。
  - 若需要减少每次独立脚本的冷启动开销，可先启动 `python tests/run_runtime.py --debug --warmup`，再在常驻控制台中连续执行 `send` / `sendc` / `add`。
  - 若后续被 HTTP 服务、任务调度器或桌面代理层集成，建议继续沿用 `ControlResponse` 作为统一返回协议，并在接入侧维护调用串行与桌面会话可用性。

## 架构特征总结

- 当前能力边界明确，围绕“发消息”和“加好友”两条自动化链路展开。
- 控制器负责编排，视觉窗口对象负责执行，层次上已形成清晰分工。
- 视觉自动化部分采用 OCR、启发式坐标和缓存混合定位，目标是提升真实桌面环境中的执行成功率。
- 项目提供命令行脚本作为调试与回归入口，便于本地复现问题。
- 当前更偏单机自动化工具形态，配置与状态保持轻量，后续若要对外服务化，可继续补充环境配置、错误治理和版本适配说明。

## 来源文件

- `.Knowledge/stock-docs/架构说明_初稿.md`
- `wxautocontrol/__init__.py`
- `wxautocontrol/api.py`
- `wxautocontrol/client.py`
- `wxautocontrol/visual.py`
- `wxautocontrol/state.py`
- `wxautocontrol/send_message.py`
- `wxautocontrol/add_friend.py`
- `wxautocontrol/common/types.py`
- `wxautocontrol/common/window.py`
- `wxautocontrol/common/win32.py`
- `wxautocontrol/common/lock.py`
- `wxautocontrol/common/log.py`
- `tests/run_add_friend.py`
- `tests/run_send_message.py`
