# wxautocontrol-architecture

一句话意图：回答当前 `wxAutoControl` 仓库的整体架构、模块分层、自动化链路与运行边界。

## 适用场景 / 触发词

适用于以下问题：

- 项目架构说明
- 仓库模块怎么分
- 添加好友和发消息分别走哪条链路
- 视觉自动化、窗口识别、坐标缓存怎么组织
- `wxautocontrol/`、`tests/`、`.Knowledge/`、`.task/` 分别负责什么

## 核心规则 / 流程

1. 先把仓库理解为一个 Windows 桌面微信自动化能力包，而不是完整业务系统。
2. 主业务边界只有两条：发送消息、通过手机号添加好友。
3. 业务编排入口在 `wxautocontrol/client.py` 的 `WeixinController`：
   - `send_msg(...)` 负责消息发送流程
   - `add_new_friend(...)` 负责添加好友流程
4. 视觉自动化主实现集中在 `wxautocontrol/visual.py`：
   - 基础截图、OCR、点击、输入由 `VisualWeixinWindow` 提供
   - 添加好友资料页、搜索窗口、加号菜单、申请弹窗分别拆成独立窗口对象
   - 坐标定位采用“缓存优先，失败回退 OCR”的策略
   - `add_menu` 搜索成功不能只依赖“添加朋友”窗口标题，必须确认已离开纯搜索表单，或已经进入可识别结果页
   - 添加好友提交后采用宽松成功判定：确认点击已发出后，仅在识别到明确失败状态时返回失败
   - 发送消息搜索只认 `联系人` 与 `群聊` 分组中的真实命中项，不再把顶部普通搜索建议、`聊天记录` 等区域当作可发送目标
   - 发送消息支持联系人命中缓存；缓存存在时优先直接 `Enter` 打开会话，失败再回退 OCR
   - 发送消息存在“极速路径”：已缓存联系人时，搜索框写入和聊天输入框定位都优先走缓存，尽量减少 OCR 次数
   - 当前会话支持连续发送：首次进入会话后，后续同会话消息可直接在当前聊天框发送，不再重新搜索
5. 公共支撑能力集中在 `wxautocontrol/common/`：
   - `window.py` 负责微信窗口识别与客户端形态判断
   - `lock.py` 负责 UI 串行化
   - `types.py` 提供配置常量、统一返回结构、添加好友节流状态和最近消息会话状态
   - `log.py` 与 `win32.py` 提供日志和底层系统交互
6. `tests/` 不是单元测试目录，而是手工运行脚本入口：
   - `run_add_friend.py`
   - `run_send_message.py`
   - `run_runtime.py`：常驻控制台模式，支持 `warmup`、`send`、`sendc`、`add`、`refresh`

## 返回约定

- 顶层返回协议固定为 `status`、`code`、`message`、`data`。
- 顶层 `status` 只使用 `success` 或 `error`。
- 添加好友成功时，顶层 `code` 固定返回 `ADD_FRIEND_SUCCESS`。
- 当 `data.status=already_friend` 时，顶层 `code` 固定返回 `ADD_FRIEND_ALREADY_ADDED`。
- 其他添加好友失败结果，顶层 `code` 固定返回 `ADD_FRIEND_FAILED`。
- 发送消息成功时，顶层 `code` 固定返回 `SEND_MESSAGE_SUCCESS`；失败统一返回 `SEND_MESSAGE_FAILED`，异常和中断分别映射为 `SEND_MESSAGE_EXCEPTION`、`SEND_MESSAGE_INTERRUPTED`。

## 架构边界

- 本主题回答“当前仓库是怎么组织的、能力怎么分层、入口怎么串起来”。
- 本主题不替代具体业务实现细节排查；若用户问某个 UI 状态、按钮定位或脚本失败原因，需要继续下钻业务代码。
- 本主题不用于 Flow2Spec 技能路由、`.task/` 追踪规则或 `req-docs` 技术方案实现，这些应走各自已有 topic。

## 子主题

- **[操作完成判定机制](wxautocontrol-completion-detection.md)**：详述缓存模式下如何判断操作完成、窗口状态检测、宽松成功判定等实现细节

## 关联文档

- [wxAutoControl 架构说明终稿](../stock-docs/wxAutoControl_架构说明_终稿.md)
- [架构说明初稿](../stock-docs/架构说明_初稿.md)
