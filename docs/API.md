# API

基础：`http://127.0.0.1:8000`，除标注外都需要 `Authorization: Bearer <access_token>`。
错误统一为 `{"error": {"code": "...", "message": "...", "details": ...}}`，HTTP 状态码与 code 稳定。

## 1. 认证

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| POST | `/api/auth/register` | `{email, password, display_name, venue, affiliation, affiliation_domain}` |
| POST | `/api/auth/login` | `{email, password}` → `{access_token, refresh_token, user}` |
| POST | `/api/auth/refresh` | `{refresh_token}`（轮换） |
| POST | `/api/auth/logout` | 撤销全部 refresh |
| GET | `/api/auth/me` | 当前身份 + onboarding 状态 |

## 2. 建档与隐私

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| GET | `/api/onboarding/borrow-prompt` | 返回"10 秒借壳"Prompt 模板 |
| POST | `/api/onboarding/anchor` | `{papers[], topics[], github, orcid, homepage}` 公开锚点 |
| POST | `/api/onboarding/borrow` | `{text, sandbox_consent}` 粘贴模型输出 |
| POST | `/api/onboarding/narrative` | `{narrative, sandbox_consent}` 60 秒自述（有 live 模型时用模型抽取，否则规则抽取） |
| POST | `/api/onboarding/preview` | `{topics[]}` 只算泛化不落库（"我出去的是什么"） |
| GET | `/api/me/profile` | 公开底板 + 泛化结果（含 `raw_fingerprint`、`internal_tags`、`cohort_size`）+ 私密档案（本人可见）+ 凭证 |
| POST | `/api/me/credentials` | `{kind, subject}`：只验格式与域名，返回 `status` |
| DELETE | `/api/me/vault` | 销毁私密档案，匹配权重归零 |
| GET | `/api/me/transparency` | 存了什么 / 从不存什么 / 保留期 / 审计事件 |

## 3. 卡片与圆桌

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| GET | `/api/cards` | 今日卡片 + 配额；未解锁的 `peer` 只有匿名代号 |
| GET | `/api/cards/quota` | `{cap, used, remaining}` |
| POST | `/api/cards/{id}/act` | `{action: accept|decline, slot_id?}`；双边 accept 才 `unlocked` |
| GET | `/api/groups` | 我所在的圆桌 + 议程 |
| GET | `/api/groups/{id}` | 单个圆桌（非成员 403） |
| POST | `/api/groups/{id}/join` · `/leave` | 入组 / 退组 |

## 4. 近场

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| GET | `/api/proximity/venues` | 当前场馆的区域清单 |
| POST | `/api/proximity/checkin` | `{session_ref, visible}` → 15 分钟轮换信标 |
| GET | `/api/proximity/radar` | 同场馆同时间桶、有共同方向的匿名同行 |
| POST | `/api/proximity/ping` | `{ref}` 打招呼（消耗对方一张配额） |
| POST | `/api/proximity/checkout` | 离场，信标立刻失效 |

## 5. 门卫

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| GET/PUT | `/api/gatekeeper/policy` | `{enabled, pitch_threshold, stranger_slots_per_day, rules}`（公平底线不可关） |
| POST | `/api/gatekeeper/requests` | `{target_id, ask, why_you, evidence, time_ask}` |
| GET | `/api/gatekeeper/incoming` · `/outgoing` | 收到的 / 发出的申请（含 reason code 与人类可读解释） |

## 6. 跑批与运维

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| POST | `/api/runs` | 触发一次跑批（生产仅 admin，`sandbox` 限流桶） |
| GET | `/api/runs` · `/api/runs/{id}` | 跑批列表 / 明细（会话只返回摘要与风险标记，`transcript: "not_stored"`） |
| GET | `/api/admin/stats` | 用户/画像/沙盒/门控/卡片/圆桌/同簇直方图 |
| GET | `/api/admin/cost` | 当日成本报表（按 tier 拆分） |
| GET | `/api/admin/audit` | 最近已销毁场次（含 guarantee 文案） |
| GET | `/api/admin/pool` | 各场馆可用画像数与 k 判定 |
| GET | `/api/config` | 公开配置（场馆、k、预算、意图词表、演示账号）——**无需鉴权** |
| GET | `/api/health` | 健康检查 |

## 7. A2A（Agent2Agent 0.2.5）

| 方法 | 路径 | 鉴权 | 说明 |
| :-- | :-- | :-- | :-- |
| GET | `/.well-known/agent.json` | 无 | 平台 AgentCard（含 extension） |
| POST | `/a2a/v1` | `X-Resonance-Peer: <harness token>` | JSON-RPC 2.0：`message/send`、`tasks/get`、`agent/getCard`、`ping` |
| GET | `/api/harness/inbox` | 同上 | 今日卡片 + 配额 + 平台策略（给本地 harness 拉取） |
| POST | `/api/harness/reply` | 同上 | `{card_id, action, slot_id?}` 代主人做决定 |
| GET/POST/DELETE | `/api/harness/bindings` | Bearer | 创建/列出/吊销 peer 凭据（token 只显示一次） |

### message/send 示例

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{
        "kind": "text",
        "text": "{\"target_handle\":\"u_abc123\",\"pitch\":{\"ask\":\"想聊 15 分钟你们的分桶边界\",\"why_you\":\"我在同一方向做过实测，可分享数据\",\"evidence\":\"P99 从 800ms 到 520ms，代价是吞吐掉 6%\",\"time_ask\":\"15min\"}}"
      }]
    }
  }
}
```

响应只给 coarse 结果，**永远不含对方画像**：

```json
{
  "jsonrpc": "2.0", "id": 1,
  "result": {"task": {
    "id": "9d0f…", "contextId": "1c2e…",
    "status": {"state": "completed", "message": "{\"verdict\":\"pass\",\"tier\":2,\"score\":78,\"reason_code\":\"tier2_passed\",\"note\":\"结构化 Pitch 通过，已进入对方今日卡片。\"}"}
  }}
}
```

### Extension schema（AgentCard 里嵌入）

```json
{
  "uri": "https://resonance.conference/a2a/ext/v1",
  "params": {
    "venue": "sydney",
    "generalized_tags": ["kv_cache"],
    "k_anonymity": {"k": 10, "cohort_key": "0e8b3a18…"},
    "credential_summary": [{"kind": "orcid", "status": "verified"}],
    "transcript_policy": "never_persisted",
    "attention_budget": {"cards_per_day": 3}
  }
}
```

`transcript_policy` 是常量而不是配置项：这个 API 里没有取回原始对话的路径，`sandbox_sessions` 表里也没有那一列。

## 8. 导出

| 方法 | 路径 | 说明 |
| :-- | :-- | :-- |
| GET | `/api/export/markdown` | 参会纪要（已解锁连接 / 破冰切入点 / 圆桌议程 / 偏好校准 / 隐私账本） |
| GET | `/api/export/agent-card` | A2A 0.2.5 AgentCard + extension |

## 9. 错误码

| code | HTTP | 含义 |
| :-- | --: | :-- |
| `invalid_credentials` | 401 | 邮箱或密码错 |
| `missing_token` / `invalid_token` | 401 | 未登录 / 过期 |
| `attention_budget_exhausted` | 429 | 对方或自己今日配额用完 |
| `privacy_invariant_violation` | 422 | 操作会破坏 k 匿名不变量 |
| `rate_limited` | 403 | 限流桶耗尽（带 retry 秒数） |
| `recipient_budget_exhausted` | 429 | 被投递方配额已满 |
| `stale_beacon` | 403 | 近场信标过期（15 分钟轮换） |
| `gatekeeper_disabled` | 403 | 对方没开门卫 |
| `admin_required` | 403 | 生产环境触发跑批需要管理员 |
