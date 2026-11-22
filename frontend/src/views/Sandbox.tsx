import { useState } from "react";
import { api } from "../api";
import { Empty, Meter, Panel, Stat, Spinner, useAsync, useToast, timeAgo } from "../components/ui";
import type { SandboxSessionRow } from "../types";

const STAGES = [
  { n: "FILTER", t: "硬过滤", d: "盲索引互斥 / 同机构排除" },
  { n: "RECALL", t: "向量粗排", d: "零 LLM 成本，Top 20 候选" },
  { n: "SANDBOX", t: "封闭沙盒", d: "两轮互考 + 脱离上下文打分" },
  { n: "GATE", t: "双盲门控", d: "双向达标才解锁" },
];

export default function Sandbox() {
  const toast = useToast();
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [lastRun, setLastRun] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [venue, setVenue] = useState("sydney");
  const [pairs, setPairs] = useState(60);
  const runs = useAsync(() => api.runs(), []);
  const stats = useAsync(() => api.stats(), []);
  const cost = useAsync(() => api.cost(), []);
  const audit = useAsync(() => api.auditSessions(), []);

  async function trigger() {
    setRunning(true);
    setProgress(0);
    setLastRun(null);
    const timer = window.setInterval(() => setProgress((value) => Math.min(96, value + 7)), 220);
    try {
      const result = await api.triggerRun({ venue, max_pairs: pairs, pairs_per_user: 3 });
      setLastRun(result);
      const full = await api.runDetail(result.run_id);
      setDetail(full);
      toast.push(`跑批完成：${result.stats.sessions} 场沙盒，${result.stats.gated} 对解锁`);
      runs.reload();
      stats.reload();
      audit.reload();
      cost.reload();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      window.clearInterval(timer);
      setProgress(100);
      window.setTimeout(() => setProgress(0), 900);
      setRunning(false);
    }
  }

  const runStats = lastRun?.stats ?? runs.data?.runs?.[0]?.stats;
  const burned = runStats?.burned ?? {};
  const engine = lastRun?.engine ?? runs.data?.runs?.[0]?.engine ?? stats.data?.engine ?? "-";

  return (
    <div className="grid">
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 02 · CLOSED SANDBOX</div>
          <h1>凌晨两点，Agent 在你看不见的地方交锋</h1>
          <p className="lede">
            这不是「推荐列表」。两个 agent 被拉进一个阅后即焚的封闭沙盒：先各抛一个具体卡点，再互相追问一层技术细节，
            然后脱离彼此上下文独立打分。只要有一边没到线，记录当场硬删除——对方永远不会知道你来过。
          </p>
        </div>
        <div className="chips">
          <span className="chip cyan">engine: {engine}</span>
          <span className="chip">transcript: never_persisted</span>
        </div>
      </div>

      <Panel
        title="跑一次今天的暗池"
        right={<span className="chip">rate limited · admin-only in production</span>}
      >
        <div className="btn-row" style={{ alignItems: "center" }}>
          <select value={venue} onChange={(event) => setVenue(event.target.value)} style={{ width: 190 }}>
            <option value="sydney">Sydney · ICC</option>
            <option value="atlanta">Atlanta · Satellite</option>
            <option value="paris">Paris · Satellite</option>
          </select>
          <select value={pairs} onChange={(event) => setPairs(Number(event.target.value))} style={{ width: 190 }}>
            <option value={20}>最多 20 对（快，演示用）</option>
            <option value={60}>最多 60 对</option>
            <option value={120}>最多 120 对</option>
          </select>
          <button className="btn btn-primary" onClick={trigger} disabled={running}>
            {running ? <span className="spinner" /> : null}
            启动批处理
          </button>
        </div>
        <div className="mt">
          <div className="meter" aria-label="progress">
            <span style={{ width: `${progress}%`, transition: "width 200ms linear" }} />
          </div>
        </div>
        <div className="pipeline mt">
          {STAGES.map((stage, index) => {
            const active = running && progress > index * 22 && progress <= (index + 1) * 26;
            const done = progress > (index + 1) * 24;
            return (
              <div key={stage.n} className={`stage ${active ? "active" : ""} ${done ? "done" : ""}`}>
                {active && <span className="stage-pulse" />}
                <div className="stage-n">{stage.n}</div>
                <div className="stage-t">{stage.t}</div>
                <div className="stage-d">{stage.d}</div>
              </div>
            );
          })}
        </div>
      </Panel>

      <div className="stats">
        <Stat label="候选人数" value={runStats?.candidates ?? "—"} hint="通过硬过滤的参会者" />
        <Stat label="沙盒场次" value={runStats?.sessions ?? "—"} hint={`配对上限 ${pairs}`} />
        <Stat
          label="双盲放行"
          value={runStats?.gated ?? "—"}
          hint={runStats?.sessions ? `${Math.round((runStats.gated / runStats.sessions) * 100)}% 通过率` : "—"}
        />
        <Stat label="本次成本" value={`$${(runStats?.cost_usd ?? 0).toFixed(4)}`} hint="确定性引擎为 0" />
        <Stat label="发出卡片" value={runStats?.cards ?? "—"} hint={`${runStats?.card_recipients ?? 0} 人收到`} />
        <Stat label="今日模型花费" value={`$${(cost.data?.total_usd ?? 0).toFixed(4)}`} hint={`${cost.data?.sessions ?? 0} 场`} />
      </div>

      <div className="grid grid-2">
        <Panel title="为什么被销毁（本次）">
          {Object.keys(burned).length === 0 ? (
            <Empty>还没有跑批，或者全部通过了。</Empty>
          ) : (
            <div className="bar-list">
              {Object.entries(burned).map(([reason, count]) => (
                <div key={reason} className="bar-item">
                  <span className="mono tiny">{reason}</span>
                  <div className="bar-track">
                    <div
                      className="bar-fill"
                      style={{ width: `${Math.min(100, ((count as number) / Math.max(1, runStats.sessions)) * 100)}%` }}
                    />
                  </div>
                  <span className="num tiny right">{count as number}</span>
                </div>
              ))}
            </div>
          )}
          <div className="notice mt">
            单向不达标 → 静默销毁；被判定为恶意注入 → 整场中止并记一条审计事件。两种都不会推给任何人。
          </div>
        </Panel>

        <Panel title="画像池健康度">
          <div className="kv">
            <div className="kv-row">
              <div className="kv-k">已发布画像</div>
              <div className="kv-v num">{stats.data?.published_profiles ?? "—"}</div>
            </div>
            <div className="kv-row">
              <div className="kv-k">最小同簇</div>
              <div className="kv-v">
                <span className={`chip ${(stats.data?.min_cohort ?? 0) >= (stats.data?.k_target ?? 10) ? "lime" : "warn"}`}>
                  {stats.data?.min_cohort ?? "—"} / k={stats.data?.k_target ?? 10}
                </span>
              </div>
            </div>
            <div className="kv-row">
              <div className="kv-k">加密档案</div>
              <div className="kv-v num">{stats.data?.vaults ?? "—"}</div>
            </div>
            <div className="kv-row">
              <div className="kv-k">微圆桌</div>
              <div className="kv-v num">{stats.data?.groups ?? "—"}</div>
            </div>
            <div className="kv-row">
              <div className="kv-k">当日卡片</div>
              <div className="kv-v num">{stats.data?.cards ?? "—"}</div>
            </div>
          </div>
        </Panel>
      </div>

      <Panel title="最近场次审计" right={<span className="chip">{audit.data?.guarantee ?? ""}</span>}>
        {audit.loading ? (
          <Spinner />
        ) : (audit.data?.sessions ?? []).length === 0 ? (
          <Empty>还没有沙盒场次。</Empty>
        ) : (
          <div className="term">
            {(audit.data?.sessions ?? []).slice(0, 14).map((row: SandboxSessionRow) => (
              <div key={row.id} className="ln">
                <span className="t">{row.digest.slice(0, 8)}</span>
                <span>
                  <span className={row.status === "gated" ? "ok" : "dead"}>{row.status === "gated" ? "gated · 双方达标" : row.burn_reason || row.status}</span>{" "}
                  <span className="kind">· {row.rounds}轮 · 脱敏{row.redactions}处</span>{" "}
                  {row.risk_flags?.length > 0 && <span className="warn">· {row.risk_flags.join(",")}</span>}{" "}
                  <span className="kind">· ${row.cost_usd.toFixed(5)}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="跑批历史">
        {(runs.data?.runs ?? []).length === 0 ? (
          <Empty>还没有跑批记录。</Empty>
        ) : (
          <div className="gap-sm">
            {(runs.data?.runs ?? []).map((run: any) => (
              <div key={run.id} className="row-between">
                <div className="small">
                  <span className="mono muted">{run.id.slice(0, 12)}</span> · {run.venue} · {run.engine}
                  <div className="tiny muted">
                    {timeAgo(run.started_at)} · {run.triggered_by}
                  </div>
                </div>
                <div className="chips">
                  <span className="chip">{run.stats?.sessions ?? (run.stats?.manual ? 1 : 0)} 场</span>
                  <span className="chip lime">{run.stats?.gated ?? 0} 放行</span>
                  <span className="chip">{run.stats?.cards ?? 0} 卡</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {detail && (
        <Panel title={`本次明细 · ${detail.run.id}`}>
          <div className="kv">
            <div className="kv-row">
              <div className="kv-k">场次</div>
              <div className="kv-v num">{detail.sessions.length}（展示前 200 条）</div>
            </div>
            <div className="kv-row">
              <div className="kv-k">对话原文</div>
              <div className="kv-v">
                <span className="chip red">not_stored</span>{" "}
                <span className="tiny muted">这不是权限问题，是列不存在。</span>
              </div>
            </div>
          </div>
          <div className="term mt">
            {detail.sessions.slice(0, 20).map((session: any) => (
              <div key={session.id} className="ln">
                <span className="t">{session.digest}</span>
                <span>
                  <span className={session.status === "gated" ? "ok" : "dead"}>{session.burn_reason}</span>{" "}
                  <span className="kind">· {session.rounds} 轮 · {session.redactions} 处脱敏</span>
                  {session.risk_flags?.length > 0 && <span className="warn"> · {session.risk_flags.join(",")}</span>}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}

