# 运维手册

## 1. 部署形态

### 本地 / 演示

```powershell
cd backend
uv venv .venv --python 3.10
uv pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m app.cli init
.\.venv\Scripts\python.exe -m app.cli seed
cd ..\frontend && npm install && npm run build
cd ..\scripts && .\serve-demo.ps1            # -Offline 用确定性引擎
```

### 容器 / 生产

```bash
cp .env.example .env
# 填 SECRET_KEY / VAULT_MASTER_KEY / BLIND_INDEX_KEY / DATABASE_URL / LLM_*
docker compose up --build -d
```

生产必须满足（`resonance doctor` 会把不满足项列为 blocker，退出码 2）：

- `SECRET_KEY` 不是默认值；
- `VAULT_MASTER_KEY`、`BLIND_INDEX_KEY` 显式设置（32 字节 base64）；
- `DATABASE_URL` 指向 PostgreSQL，不是 SQLite。

生成密钥：

```bash
python -c "import secrets;print(secrets.token_urlsafe(48))"                              # SECRET_KEY
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"            # VAULT_MASTER_KEY / BLIND_INDEX_KEY
```

## 2. 每日节奏

| 时间 | 动作 | 命令 |
| :-- | :-- | :-- |
| 02:00 | 夜间跑批（各场馆） | `resonance run --venue sydney`（建议 cron / 定时任务） |
| 08:00 | 卡片由用户端拉取（无推送） | 无 |
| 随时 | 健康检查 | `GET /api/health`、`GET /api/admin/pool` |
| 会后 | 导出与会后清理 | 用户侧导出；保留期到点后清理 vault |

跑批是幂等的：同一天重复跑不会重复发卡（`quota_ledger` 把关），重复的圆桌不会被重建（组 ID 由成员集合决定）。若需要重跑当天交付：

```bash
resonance flush            # 清空当日卡片/配额/匹配/场次/圆桌，不动用户与画像
resonance run --venue sydney
```

## 3. 故障处置

| 症状 | 先看什么 | 处置 |
| :-- | :-- | :-- |
| 跑批长时间不结束 | `GET /api/runs` 的最后一条 `status`；日志里 `sandbox_pair_failed` | 单对失败不影响整批；若是整体卡住，检查 `LLM_*` 连通性与并发设置 |
| 全部场次 `upstream_failure` | `resonance doctor` 的 `gateway.reachable` | 模型网关不可达 → 临时 `LLM_MODE=deterministic` 保服务，恢复后切回 |
| 全部场次 `both_below_threshold` | judge 模型是否变了 | 见第 4 节校准 |
| 全部场次 `injection_attempt` | judge/small 模型是否在复述系统提示 | 检查 `prompt:schema_probe` 类风险；必要时把 `rewrite` 阈值收紧（`pii.INJECTION_PATTERNS`） |
| 卡片普遍被"没收"（`disclosure.ok=false`） | `GET /api/admin/stats` 的 `min_cohort` | 池子太薄：扩大参会池或临时下调 `K_ANON_MIN`（**不要**低于 5，生产 warn） |
| 用户说"没收到卡" | `quota_ledger` 与 `cards` | 三种可能：配额用尽 / 单向不达标（正常）/ 披露守卫拦下（看 `payload.disclosure.notes`） |
| 缴费/配额异常 | `quota_ledger` 当日行 | `resonance flush` 会重置当日配额 |

## 4. 阈值校准（换 judge 模型必做）

门控阈值是**per-model 校准量**，不是普适常数。换模型后按下面三步重定：

```bash
# 1) 跑一批，观察通过率与 min_score 分布
resonance run --venue sydney --max-pairs 24

# 2) 看分布
python - <<'PY'
import sqlite3, statistics
con = sqlite3.connect("data/resonance.db")
mins = sorted(min(a, b) for a, b in con.execute("select score_a, score_b from match_results"))
print("n", len(mins), "min", mins[:3], "median", statistics.median(mins), "max", mins[-3:])
PY

# 3) 定阈值：把"通过率"调到 20–35%
```

经验：本机用 `qwen3.6-35b-fast` 时，阈值 80 → 全放行（83%），85 → 约 50%，87 → 约 15%。选 85 的理由不是数学，而是**噪声控制已经由每日 3 张卡的预算承担**，门控只负责"不把明显不合适的人推给你"。

## 5. 监控建议

必须有告警的四条：

1. `cost_ledger` 中 `tier='large'` 的调用数 / 场次数 > 0.5（说明破绽润色被滥用）；
2. 跑批通过率连续两晚 < 5% 或 > 70%（模型漂移或阈值失配）；
3. `sandbox_sessions.risk_flags` 里 `injection:*` 的比例 > 2%（有人在批量投毒）；
4. `admin/pool.verdict != ok`（画像池跌破 k，系统会开始大量丢标签）。

## 6. 备份与保留

- 备份 `private_vaults` 时**不要**把 `VAULT_MASTER_KEY` 放在同一份备份里；
- 会用后 30 天清理 vault 与 cards（保留期写在 `/api/me/transparency`）；
- 用户主动删除走 `DELETE /api/me/vault`：密文置空、盲索引删除、画像失活，历史审计事件保留（不含内容）。

## 7. 本地 Harness 接入

```jsonc
// Claude Code / Cursor 的 MCP 配置
{
  "mcpServers": {
    "resonance": {
      "command": "node",
      "args": ["<repo>/harness/src/index.mjs"],
      "env": {
        "RESONANCE_URL": "http://127.0.0.1:8000",
        "RESONANCE_PEER_TOKEN": "<在 Web 端「带走资产」页生成的 token>"
      }
    }
  }
}
```

**必须**给这个 MCP 配置一个只读、无终端权限的会话：桥本身只有三个 HTTP 工具，但如果把会议数据粘进一个有高权限的工作区，风险就转移到用户侧了。

## 8. 常用命令

```bash
resonance init                 # 建表 + 跑迁移
resonance seed --population 240
resonance rebuild              # 批量导入后重算 k 匿名投影
resonance run --venue sydney --max-pairs 40
resonance pair --a chen@demo.resonance --b maya@demo.resonance   # QA：指定一对走完整沙盒
resonance flush [--force]      # 清当日交付
resonance doctor               # 配置/网关/成本/生产 blocker
resonance reset                # 删库（开发用，生产拒绝）
resonance serve --port 8000 --reload
```

