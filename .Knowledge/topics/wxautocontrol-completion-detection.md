# wxautocontrol-completion-detection

一句话意图：解释 wxAutoControl 如何判断添加好友、发送消息等操作是否完成，特别是在使用缓存时的完成判定机制。

## 适用场景 / 触发词

适用于以下问题：

- 怎么判断操作完成
- 缓存模式下如何判断成功
- 使用缓存时没有OCR怎么知道完成了
- 发送消息如何确认已发出
- 添加好友如何判断提交成功
- 操作完成判定机制
- 窗口状态检测
- 宽松成功判定

## 依赖声明

执行前须先读依赖主题 `wxautocontrol-architecture`（了解整体架构与缓存策略）。

## 核心规则 / 流程

### 1. 发送消息完成判定（`send_message_to_session`）

**核心机制**：通过 **窗口状态变化检测** 判断操作完成，不完全依赖 OCR。

**判定逻辑**（`wxautocontrol/visual.py:1365-1410`）：

1. **缓存路径**（`wxautocontrol/visual.py:1368-1377`）：
   - 检测到缓存坐标 → 直接按 `Enter` 键
   - 等待 `post_click_settle` 时间（默认 0.18秒）
   - **关键判断**：调用 `VisualSearchPopup.find(timeout=0.08)`
     - 返回 `None`（搜索弹窗消失）→ 操作成功，已进入会话
     - 返回窗口对象（弹窗仍存在）→ 缓存失效，回退 OCR

2. **回退 OCR**（`wxautocontrol/visual.py:1378-1397`）：
   - 清除失效缓存：`popup.clear_cached_contact_entry(who)`
   - 使用 OCR 查找联系人条目
   - 找到后点击并记录新缓存坐标

3. **最终判定**：
   - 成功进入聊天输入框（`wxautocontrol/visual.py:1399-1410`）→ 返回 `"sent"`
   - 重试后仍未找到联系人 → 返回 `"session_not_found"`

**窗口检测方法**：`VisualSearchPopup.find()` 通过 Windows API 查找指定窗口句柄（窗口类 `Qt51514QWindowToolSaveBits` + 标题 `Weixin`），不依赖 OCR 文本识别。

### 2. 添加好友完成判定（`add_new_friend`）

**核心机制**：采用 **宽松成功判定**（optimistic success inference）。

**判定逻辑**（`wxautocontrol/visual.py:2020-2032`）：

```python
def infer_post_submit_success(self, submit_ok: bool, timeout: float = 4.0) -> str:
    post_state = self.detect_post_apply_state(timeout=timeout)
    # 明确失败状态
    if post_state in {"rate_limited", "permission_required"}:
        return post_state
    # 明确成功状态
    if post_state == "applied":
        return post_state
    # 宽松判定：点击已发出 + 无明确失败 = 乐观返回成功
    if submit_ok:
        return "applied"
    return post_state
```

**状态检测方法**（`wxautocontrol/visual.py:1998-2018`）：

在 `timeout` 时间内循环检测以下状态（使用 OCR + 窗口检测）：

- **成功状态**：
  - 检测到"等待验证"按钮 → `"applied"`
  - 找到好友申请窗口 → `"request_form"`

- **明确失败状态**（OCR 检测特定文本）：
  - "操作过于频繁" → `"rate_limited"`
  - "账号异常" → `"rate_limited"`
  - "联系人较多" → `"permission_required"`

- **超时未检测到任何状态** → `"unknown"`

**宽松判定原则**：
- 只要点击动作已发出（`submit_ok=True`），且未检测到明确失败文本，就返回成功
- 避免因 UI 响应慢、OCR 漏检等原因误报失败
- 适用于提交类操作的容错设计

### 3. 时序逻辑与 settle 参数

**settle 参数**：操作后等待时间（秒），用于等待 UI 稳定。

**关键时序点**（`wxautocontrol/visual.py:1345-1348`）：
```python
popup_timeout = min(0.45, max(0.25, settle + 0.1))      # 弹窗出现等待
miss_sleep = min(0.12, max(0.06, settle * 0.3))         # 未找到时重试间隔
popup_click_settle = min(0.28, max(0.18, settle))       # 点击后等待弹窗消失
post_click_settle = min(0.18, max(0.10, settle * 0.5))  # 点击后进入下一步
```

**设计目的**：
- 适应不同机器性能（通过 settle 参数调整）
- 给 UI 动画、网络请求留出响应时间
- 在性能约束下平衡速度与可靠性

## 判定机制对比

| 操作类型 | 主要判定方式 | 是否需要 OCR | 失败回退 |
|---------|------------|------------|---------|
| **发送消息（缓存）** | 窗口状态检测（弹窗消失） | 否 | 清除缓存 + OCR 查找 |
| **发送消息（无缓存）** | OCR 查找联系人 + 窗口状态 | 是 | 重试 3 次 |
| **添加好友** | OCR 检测状态文本 + 宽松判定 | 是（检测失败状态） | 返回明确失败码 |

## 边界与禁止项

- 本主题聚焦"如何判断操作完成"，不涉及具体 UI 定位、坐标缓存实现细节
- 窗口检测的底层 Windows API 调用细节不在本主题范围
- 具体 OCR 配置、识别精度调优等应查阅 `wxautocontrol/visual.py` 源码

## 关联文档

- [wxAutoControl 架构说明终稿](../stock-docs/wxAutoControl_架构说明_终稿.md)
