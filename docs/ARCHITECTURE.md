# 架构

## 1. 为什么是这个形状

这套系统的核心矛盾是：**撮合需要真信息，社交需要假身份。**

- 撮合质量取决于"你最近卡在哪个环节""你真正想要什么"，这些是简历上不会写的私密增量；
- 而一旦这些信息以可反查的形式离开你，整套机制就死了——对方不用套话，两条 Google Scholar 检索就能反推出你是谁。

所以架构的主轴不是"推荐系统"，而是**一条受控的信息漏斗**：越往人靠近，信息越少；越往机器内部，信息越细。

```
本地 Harness（原始笔记/代码/日常 AI 上下文）
   │  只上传脱敏后的结构化简报（10 秒借壳 / 60 秒自述）
   ▼
加密私密档案  PrivateVault  (AES-256-GCM，密钥从 SECRET_KEY 派生)
   │  等值比对走盲索引，匹配时不需解密全量
   ▼
k-匿名标签包  GeneralProfile  (对外发布，同簇 ≥ k)
   │
   ├──► 封闭沙盒（两份完整简报临时解密进内存，跑完即焚）
   │       两轮匿名互考 → 脱离上下文打分 → 双盲门控
   ▼
卡片 / 圆桌 / 近场提示  (人类可见面，只含 k-匿名标签 + 脱敏后的证据句)
```

## 2. 四层主干

| 层 | 职责 | 代码 |
| :-- | :-- | :-- |
| **触点接入** | 三条建档路径（借壳 / 自述 / 公开锚点）、MCP 中继、A2A JSON-RPC | `api/routes_attendee.py`、`api/routes_a2a.py`、`harness/` |
| **信任与上下文** | 凭证核验、门卫分层、近场信标、k-匿名网关 | `domain/credentials.py`、`domain/gatekeeper.py`、`domain/proximity.py`、`domain/generalize.py` |
| **核心 A2A 引擎** | 暗池双盲沙盒、微组局 | `domain/sandbox.py`、`domain/scoring.py`、`domain/groups.py`、`services/matching.py` |
| **运行时底座** | 加密存储、盲索引、向量召回、分级推理、成本账本 | `core/security.py`、`db/`、`llm/`、`services/costs.py` |

横向的**治理总线**：脱敏网关（`domain/pii.py`）、披露守卫（`domain/disclosure.py`）、注意力预算（`domain/budget.py`）、审计事件（`AuditEvent`）。任何一条路径产生人类可见文本，都必须经过前两个。

## 3. 关键数据模型

| 表 | 存什么 | 不存什么 |
| :-- | :-- | :-- |
| `private_vaults` | AES-GCM 密文 + 质量分 + 授权标记 | 明文诉求 |
| `vault_blind_indexes` | HMAC(kind, value) 的等值标记 | 可逆的原文 |
| `general_profiles` | 可发布的 k-匿名标签 + 沙盒专用内部标签 + 向量 | 原始论文标题 |
| `sandbox_sessions` | 场次元数据、摘要哈希、脱敏计数、风险标记、成本 | **对话原文（没有这一列）** |
| `match_results` | 双向分数、门控结果、脱敏后的证据句 | 对话原文 |
| `matches` / `cards` / `quota_ledger` | 解锁状态、卡片、配额 | — |
| `anchor_registry` | 摄取到的公开 artefact 与标签 | — |
| `cost_ledger` | 每次模型调用的 tier/model/tokens/usd | — |

设计上刻意区分两套标签：

- `general_profiles.tags` 是**可发布**的，经过 k-匿名与同簇校验，进入卡片、导出、AgentCard；
- `general_profiles.internal_tags` 是**沙盒内部**的细粒度类目，只用于召回与让 agent 说得出技术细节。

把两者混成一套的结果是实测过的：k 匿名约束套在匹配特征上，会让每个人的画像塌缩成 1 个标签（206/206 个画像都是 1 个标签），匹配质量直接归零。**隐私控制 quietly 杀死产品**是这类系统最典型的失败模式，所以在结构上把"对外披露"与"内部召回"分开，而不是靠调参。

## 4. 一次夜间跑批的完整流程

`services/matching.py:run_nightly`

```
1. load_candidates(venue)
   ├─ 拉 User ⋈ GeneralProfile(active) ⋈ PrivateVault(未销毁)
   ├─ 批量读盲索引 → 每个候选人一个哈希集合
   └─ 解密各自 vault（仅内存），计算行为校准阈值（历史接受率过高 → 抬高自己的门槛）

2. recall_pairs()
   ├─ 硬过滤：同机构 / 双向域名屏蔽（盲索引，不解密）/ 意图互补 / 已有配对排除
   ├─ 向量粗排：cos(内部标签向量) + 互补加分，每人 Top-K
   └─ 配额分配：每人最多 N 对，按亲和度降序领票

3. asyncio.gather(run_pair)   ← 并发段内不碰数据库
   ├─ Round 1 匿名探针（各自抛一个具体卡点 + 一个分真假的问题）
   ├─ Round 2 深度互考（回答对方的追问，必须给机制/权衡/踩坑）
   ├─ 每句话：引用核查 → 强脱敏 → 注入扫描 → 记录风险标记
   ├─ 判定任一方向存在指令形状文本 → 整场中止（injection_attempt）
   ├─ Round 3 脱离上下文独立打分（JSON，固定字段）
   ├─ 证据落地：无法追溯到对话或真实 artefact 的证据句被替换成可归因表述
   └─ burn()：清空内存中的对话，返回 SHA-256 摘要

4. 顺序持久化（并发结束后）
   ├─ sandbox_sessions（摘要 + 风险 + 成本，无原文）
   ├─ 通过门控 → match_results + matches(state=locked)
   └─ audit_events（双方各一条）

5. form_groups() → 3–6 人角色互补圆桌 + 50 分钟议程

6. allocate_cards()
   ├─ 披露守卫：卡片标签重新过 k，不够就摘掉
   ├─ 预算分配：圆桌优先，1v1 保槽，每人每日上限
   └─ 写 cards + quota_ledger
```

并发段不访问数据库是有意的：`AsyncSession` 不是并发安全的，早期版本在 `gather` 里并发 `flush` 直接把跑批挂死。现在沙盒只产出纯数据对象，持久化在 gather 之后串行执行。

## 5. 沙盒状态机

```
opened ──► round1_done ──► round2_done ──► scored ──► gated
                              │                        └──► destroyed (阅后即焚)
                              └──► destroyed (injection_attempt / upstream_failure)
```

- 任何异常路径都会走 `finally` 里的 `burn()`，不存在"半场留下原文"的窗口；
- `SandboxSessionState.turns` 只在一次调用栈内存在；
- 风险标记（脱敏计数、`injection:*`、`unverified_anchor`、`evidence_regrounded:*`）随场次落库，供事后审计，但其中不含原文片段。

## 6. 分级推理与失败降级

| tier | 用途 | 典型模型 |
| :-- | :-- | :-- |
| small | Round 1/2 对话 | 便宜快速的非推理模型 |
| judge | 结构化打分（JSON） | **必须非推理模型**，否则 reasonig 通道吃光预算、content 为空 |
| large | 已过门配对的一句破冰润色 | 更贵的模型，调用量 ≤ 通过率的 2 倍 |

降级链：模型调用失败 → 该场 `upstream_failure` 关闭（不留半场）→ 整批继续；judge JSON 解析失败 → 用 `FALLBACK_SCORE`；证据不落地 → 用确定性引擎的证据句。**任何降级都不会导致"没有文本就解锁"**。

## 7. 存储与部署形态

- 本地：SQLite（WAL + `PRAGMA foreign_keys=ON`，靠连接级事件钩子确保每个连接都开）；
- 生产：PostgreSQL（`asyncpg`），迁移走 `app/db/migrations/NNN_*.sql`，`migrate()` 记录版本并容忍"列已存在"；
- 向量：默认 numpy 精确余弦（万人规模 O(n²) 粗排在秒级），pgvector 列在迁移中预留；
- 单进程内限流（token bucket）+ 数据库里的强配额（`quota_ledger`），前者防刷，后者防错。
