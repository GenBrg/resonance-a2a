import { useState } from "react";
import { api } from "../api";
import { Empty, Field, Panel, Stat, Spinner, useAsync, useToast, timeAgo } from "../components/ui";

export default function Gatekeeper() {
  const toast = useToast();
  const policy = useAsync(() => api.gatekeeperPolicy(), []);
  const incoming = useAsync(() => api.gatekeeperIncoming(), []);
  const outgoing = useAsync(() => api.gatekeeperOutgoing(), []);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    target_id: "",
    ask: "想用 15 分钟聊一下你们线上 P99 被长请求拖尾时的分桶边界",
    why_you: "我在做异步 rollout 调度，实测把页表回收挪到流水线空档能把尾延迟压三成，可以分享具体配比",
    evidence: "我们复现过一版调度器，P99 从 800ms 压到 520ms，代价是吞吐掉 6%",
    time_ask: "15min",
  });

  async function toggle(enabled: boolean) {
    setBusy(true);
    try {
      await api.updateGatekeeperPolicy({ enabled });
      toast.push(enabled ? "门卫已开启：陌生人需要结构化 Pitch" : "门卫已关闭");
      policy.reload();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function setThreshold(value: number) {
    try {
      await api.updateGatekeeperPolicy({ pitch_threshold: value });
      policy.reload();
    } catch (error) {
      toast.push((error as Error).message, "err");
    }
  }

  async function submitPitch() {
    setBusy(true);
    try {
      const result = await api.pitch(form);
      toast.push(`${result.verdict} · ${result.note}`);
      outgoing.reload();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  const snapshot = policy.data;

  return (
    <div className="grid">
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 06 · GATEKEEPER</div>
          <h1>门卫不是等级，是可核验的注意力分配</h1>
          <p className="lede">
            万人会议里被私信淹没不是收件箱问题，是判断问题——陌生人长得一模一样。
            门卫把陌生人请求压成一张结构化表：你想要什么、为什么是你、一件可核验的证据、占用多久。
            同时它有一个公平底线：任何人的门卫都不能完全关闭每天给陌生人的名额。
          </p>
        </div>
        <div className="chips">
          <span className="chip orange">tier 1 / 2 / 3</span>
          <span className="chip">fairness floor</span>
        </div>
      </div>

      <div className="stats">
        <Stat label="门卫状态" value={snapshot?.enabled ? "开启" : "关闭"} hint="关闭时只能走暗池" />
        <Stat label="Pitch 阈值" value={snapshot?.pitch_threshold ?? "—"} hint="低于阈值直接回绝" />
        <Stat
          label="今日陌生人名额"
          value={`${snapshot?.fairness?.remaining ?? 0}/${snapshot?.fairness?.stranger_slots_per_day ?? 1}`}
          hint="公平底线，不可关闭"
        />
        <Stat label="收到申请" value={(incoming.data?.requests ?? []).length} hint="近 30 条" />
      </div>

      <div className="grid grid-2">
        <Panel
          title="我的门卫策略"
          right={
            <button className="btn btn-sm" onClick={() => toggle(!snapshot?.enabled)} disabled={busy || !snapshot}>
              {snapshot?.enabled ? "关闭门卫" : "开启门卫"}
            </button>
          }
        >
          <div className="kv">
            <div className="kv-row">
              <div className="kv-k">Tier 1 直通</div>
              <div className="kv-v small">有可验证凭证（ORCID / 机构邮箱 / 已解锁过的熟人 / 同圆桌）直接放行</div>
            </div>
            <div className="kv-row">
              <div className="kv-k">Tier 2 审核</div>
              <div className="kv-v small">
                陌生 Pitch 需打分过关，且必须带可核验证据。当前阈值{" "}
                <input
                  type="range"
                  min={30}
                  max={95}
                  value={snapshot?.pitch_threshold ?? 70}
                  onChange={(event) => setThreshold(Number(event.target.value))}
                  style={{ verticalAlign: "middle", width: 140 }}
                />{" "}
                <span className="mono">{snapshot?.pitch_threshold ?? 70}</span>
              </div>
            </div>
            <div className="kv-row">
              <div className="kv-k">Tier 3 拦截</div>
              <div className="kv-v small">群发/推销关键词命中两次以上，直接拦掉，不占用你任何注意力</div>
            </div>
          </div>
          <div className="notice mt">
            公平底线：每天至少保留 {snapshot?.fairness?.stranger_slots_per_day ?? 1} 个陌生名额，哪怕分数只是「还不错」。
            否则高热度节点会把网络变成少数人的俱乐部，这在冷启动期是致命的。
          </div>
        </Panel>

        <Panel title="我来投一个 Pitch（走别人的门卫）">
          <Field label="对方 user id" hint="从对方的 AgentCard 或名片里拿">
            <input value={form.target_id} onChange={(event) => setForm({ ...form, target_id: event.target.value })} />
          </Field>
          <Field label="你想要什么" hint="≤240 字">
            <textarea
              value={form.ask}
              onChange={(event) => setForm({ ...form, ask: event.target.value })}
              style={{ minHeight: 62 }}
            />
          </Field>
          <Field label="为什么是你" hint="必须具体，禁止愿景">
            <textarea
              value={form.why_you}
              onChange={(event) => setForm({ ...form, why_you: event.target.value })}
              style={{ minHeight: 62 }}
            />
          </Field>
          <Field label="一件可核验的证据" hint="真实 artefact 或实测数字">
            <textarea
              value={form.evidence}
              onChange={(event) => setForm({ ...form, evidence: event.target.value })}
              style={{ minHeight: 62 }}
            />
          </Field>
          <div className="form-row">
            <Field label="占用多久">
              <select value={form.time_ask} onChange={(event) => setForm({ ...form, time_ask: event.target.value })}>
                <option value="10min">10 分钟</option>
                <option value="15min">15 分钟</option>
                <option value="20min">20 分钟</option>
                <option value="1 hour">1 小时（会被扣分）</option>
              </select>
            </Field>
            <div style={{ alignSelf: "end", paddingBottom: 10 }}>
              <button className="btn btn-primary" onClick={submitPitch} disabled={busy || form.target_id.length < 2}>
                {busy ? <span className="spinner" /> : null}
                提交
              </button>
            </div>
          </div>
        </Panel>
      </div>

      <Panel title="收到的申请">
        {(incoming.data?.requests ?? []).length === 0 ? (
          <Empty>还没有人向你提申请。门卫开着的时候，群发会被直接拦掉，所以你看到的是过滤后的结果。</Empty>
        ) : (
          <div className="gap-sm">
            {(incoming.data?.requests ?? []).map((request: any) => (
              <div key={request.id} className="ticket gatekeeper">
                <div className="ticket-head">
                  <div>
                    <div className="ticket-kind">
                      tier {request.tier} · score {request.score} · {timeAgo(request.created_at)}
                    </div>
                    <div className="ticket-title small">{request.pitch?.ask}</div>
                  </div>
                  <span className={`chip ${request.verdict === "pass" ? "lime" : request.verdict === "block" ? "red" : "warn"}`}>
                    {request.verdict}
                  </span>
                </div>
                <div className="ticket-body">
                  <div className="why">
                    <div className="row">
                      <span className="k">为什么</span>
                      <span>{request.pitch?.why_you}</span>
                    </div>
                    <div className="row">
                      <span className="k">证据</span>
                      <span>{request.pitch?.evidence}</span>
                    </div>
                    <div className="row">
                      <span className="k">判定</span>
                      <span className="mono tiny">{request.note}</span>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="我发出的申请">
        {(outgoing.data?.requests ?? []).length === 0 ? (
          <Empty>还没有发出过申请。</Empty>
        ) : (
          <div className="gap-sm">
            {(outgoing.data?.requests ?? []).map((request: any) => (
              <div key={request.id} className="row-between">
                <div className="small">
                  <span className={`chip ${request.verdict === "pass" ? "lime" : request.verdict === "block" ? "red" : "warn"}`}>
                    {request.verdict}
                  </span>{" "}
                  tier {request.tier} · {request.score} 分 · <span className="mono tiny">{request.reason_code}</span>
                  <div className="tiny muted">{request.note}</div>
                </div>
                <span className="tiny muted">{timeAgo(request.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
