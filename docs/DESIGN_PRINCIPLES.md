# 设计原则：六条铁律与它们的落地证据

原则不是标语。下面每一条都写清三件事：**它防的是什么失败**、**代码在哪里执行它**、**用什么证明它还在生效**。

---

## 一、默认隐私与渐进披露

**防的失败**：把"删掉姓名"当成匿名。真实世界里，`RL 推理加速 + 分页 KV cache + 4k star 开源引擎` 这三个特征组合全球只有一个人，两条检索就能反推出"某实验室的某某在偷偷看机会"。

**落地**：

- `domain/pii.py`：确定性脱敏（邮箱/URL/arXiv/DOI/仓库/star 数/机构后缀/中文机构名/自报姓名）。用规则而非概率模型，因为它要能被单元测试和 code review diff。
- `core/security.py`：`PrivateVault` 是 AES-256-GCM 密文，每用户派生一把子钥（HKDF）；`vault_blind_indexes` 只存 HMAC 等值标记，用于"屏蔽域名""意图互补"这类比对，**匹配时不解密全量**。
- `domain/generalize.py`：两段式泛化——先按每条标签的全局支撑度决定是否升到上层类目，再验证**整个组合**的同簇人数，不够就继续摘标签。
- `domain/disclosure.py`：最后一道闸。卡片是 prose，prose 才是泄露现场。守卫对卡片里的每一句跑脱敏，并把它披露的标签重新过同簇计数。

**证据**：

```
tests/test_pii.py                     # 7 类标识符必须被替换
tests/test_invariants.py::test_vault_ciphertext_contains_no_readable_intent
tests/test_invariants.py::test_blind_index_never_contains_plaintext
tests/test_invariants.py::test_pending_match_card_does_not_name_the_peer
tests/test_generalize.py::test_every_published_tuple_clears_k   # 200 人随机池
```

**一个反例，以及由此做的架构决定**：把 k 匿名约束同时套在"匹配用的特征"上时，206 个画像全部塌缩成 1 个标签（实测）。匹配从此只靠意向矩阵，产品事实上废掉。现在的做法是 `tags`（对外，k 匿名）与 `internal_tags`（沙盒内，细粒度）并存，边界写在 `db/models.py` 的注释里，并由 `test_invariants.py` 监督"卡片只用 publishable 标签"。

---

## 二、双盲确认，零拒绝尴尬

**防的失败**：被拒绝的社交成本。这是整个产品能不能被"想低调找机会的人"使用的分水岭——只要存在"单向可见"，暗池就退化成相亲角。

**落地**：

- `domain/scoring.py:gate`：只有 `A.fit_score ≥ A.threshold` **且** `B.fit_score ≥ B.threshold` **且**双方都没踩雷区，才算通过。门控函数不接受"取最大值"或"任一方向"。
- `services/matching.py:_persist_outcome`：未通过的配对**不写 `match_results`**，只留一条 `sandbox_sessions`（含摘要与原因码）和审计事件。数据库里没有"谁被谁拒绝"这一行。
- `services/cards.py:serialize`：`unlock.state != "unlocked"` 时，`peer` 字段只有匿名代号与场次，连 `display_name` 的 key 都不存在。
- `services/cards.py:act`：`accepted_by` 只记录自己；双方齐了才置 `unlocked` 并写 meeting。

**证据**：

```
tests/test_api.py::test_match_cards_require_both_sides_to_unlock   # 单边接受后 accepted_by_both=false 且无 meeting
tests/test_invariants.py::test_match_requires_both_accepts
```

---

## 三、强制注意力预算

**防的失败**：又一个消息炸锅的社交软件。会议场景里稀缺的不是信息，是"愿不愿意再读一条"。

**落地**：

- `db/models.py:QuotaLedger`：`(user_id, day)` 主键，`used/cap` 落库。不是前端计数器，不是内存变量。
- `domain/budget.py:allocate`：圆桌优先（一张桌子比一次 1v1 更容易转化）、1v1 必须保槽、单类型上限、去重。
- `services/cards.py:issue_card`：超额直接抛 `QuotaExceeded`；门卫的 Tier-1 直通是唯一允许绕过预算的路径（否则最高价值请求会被预算挤掉），且仍受限流约束。

**证据**：

```
tests/test_api.py::test_attention_budget_is_a_hard_ceiling     # 第 4 张卡抛异常
tests/test_invariants.py::test_card_payload_cohort_clears_k
```

**一个产品级取舍**：`MAX_PER_KIND = {group: 1, match: 2, ...}`。早期版本允许 2 张圆桌，结果是陈哲当天收到 3 张圆桌邀请、配额吃满，**那张已经通过双盲的 1v1 卡被静默挤掉**。1v1 更稀有也更贵，必须永远留槽。

---

## 四、学术引用锚定，零幻觉

**防的失败**：顶会学者对专业细节的容错率是零。agent 编造一篇不存在的 arXiv，信任瞬间归零，而且是不可逆的。

**落地**：

- `domain/anchors.py`：`AnchorRegistry` 存摄取到的真实 artefact 及其标签。`AnchorVerifier.exists/resolve` 校验"这个 id 存在"且"与本次对话主题相关"。
- `domain/sandbox.py:_sanitize_utterance`：**先核查再脱敏**。不存在的 id 直接替换为 `[REDACTED_PAPER]`，因此它永远不会进入评分模型的上下文，也不会成为"证据"。
- `domain/scoring.py:evidence_is_grounded`：证据句必须可追溯到一条已验证锚点、或对话中出现的具体词元（bigram 重叠 ≥ 3）、或含可量化细节。否则替换成确定性引擎生成的可归因表述，并记 `evidence_regrounded` 风险标记。

**证据**：

```
tests/test_sandbox.py::test_fabricated_anchor_is_stripped
tests/test_sandbox.py::test_own_forbidden_terms_never_survive
```

**一个踩过的坑**：早期 openreview 的锚点正则写成 `[A-Za-z0-9_-]{8,}`，把英文单词 `instructions` 也当成 id 替换成了 `[REDACTED_PAPER]`——于是注入检测再也匹配不到 `ignore all previous instructions`。**一个吃掉单词的校验器比没有校验器更危险**，现在要求 id 同时含字母与数字。

---

## 五、微组局优先

**防的失败**：1v1 见面的心理成本。两个人干坐着的形式对内向者几乎不可用，而"没人是主角的四人桌"可以。

**落地**：

- `domain/groups.py:form_groups`：角色覆盖（算法/系统/评测/理论…）必须 ≥ 3；同机构最多 1 人（两个同事会把混合桌变成双人对话）；3–6 人。
- `build_agenda`：由成员的 `bottleneck` 生成 50 分钟、四段的议程与三个具体问题，**禁止 PPT、禁止录音、禁止引用未公开内容**。
- `domain/budget.py:MAX_PER_KIND["group"]=1` 与 `KIND_ORDER` 让圆桌插队在 1v1 之前。

**证据**：`tests/test_invariants.py`（卡片/圆桌/disclosure）+ UI「微圆桌」页。

---

## 六、Human-in-the-Loop

**防的失败**：agent 替人类做不可逆承诺。排期、解锁身份、见面，都必须是人点的。

**落地**：所有 `matches` 初始 `locked`；`cards.act` 是唯一状态推进入口；`A2A` 的 `message/send` 只能产出"门卫请求"，不能产出解锁；`harness` 的 MCP 工具只有 `inbox / reply / pitch` 三个，且 `reply` 走的是同一个人工动作接口。

**证据**：`tests/test_api.py`（accept 流程）+ `harness/README.md` 的权限声明。

---

## 七、没有写进原则，但同样重要的三条

1. **失败要闭塞，不要留半成品**：`run_pair` 的 `finally` 保证任何异常路径都 `burn()`；跑批里单对失败不会让整批挂掉。
2. **评价口径要能被外部复核**：每条声称的保证都对应一个测试名，写在 README 表格里，任何人可以 `pytest -k` 复核。
3. **成本是设计约束**：粗排零模型成本、judge 用小模型、只有过门的配对才用大模型。这不是省钱，是因为"每场都调大模型"的方案在万人规模下不可运营。
