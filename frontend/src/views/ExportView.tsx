import { useState } from "react";
import { api } from "../api";
import { Empty, Panel, Spinner, useAsync, useToast, timeAgo } from "../components/ui";

export default function ExportView() {
  const toast = useToast();
  const transparency = useAsync(() => api.transparency(), []);
  const bindings = useAsync(() => api.bindings(), []);
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function bind() {
    setBusy(true);
    try {
      const result = await api.createBinding("mcp_bridge", "本地 Harness");
      setToken(result.token);
      bindings.reload();
      toast.push("绑定已创建，token 只显示这一次");
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function download(kind: "md" | "card") {
    try {
      if (kind === "md") await api.downloadMarkdown();
      else await api.downloadAgentCard();
      toast.push("已导出到下载目录");
    } catch (error) {
      toast.push((error as Error).message, "err");
    }
  }

  return (
    <div className="grid">
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 07 · PORTABILITY</div>
          <h1>七天之后，这东西必须能带走</h1>
          <p className="lede">
            一次性工具没人愿意往里喂真话。所以会议结束时，人脉图谱、讨论纪要、校准过的偏好档案都可以导成普通文件，
            直接喂回你日常用的 AI，或者带去下一场 ICLR。
          </p>
        </div>
      </div>

      <div className="grid grid-2">
        <Panel title="导出">
          <div className="btn-row">
            <button className="btn btn-primary" onClick={() => download("md")}>
              下载 Markdown 纪要
            </button>
            <button className="btn" onClick={() => download("card")}>
              下载 AgentCard.json
            </button>
          </div>
          <div className="kv mt">
            <div className="kv-row">
              <div className="kv-k">纪要包含</div>
              <div className="kv-v small">已解锁的连接、破冰切入点、微圆桌议程、偏好校准、隐私账本</div>
            </div>
            <div className="kv-row">
              <div className="kv-k">AgentCard</div>
              <div className="kv-v small">
                A2A 0.2.5 结构 + 本平台的 extension（k-匿名参数、凭证摘要、transcript_policy）
              </div>
            </div>
          </div>
        </Panel>

        <Panel title="接到你日常的 Harness">
          <p className="small dim">
            云端只做信箱：本地 harness 每次拉取今天的卡片，在本地结合私密上下文判断，再把结论推回来。
            原始笔记和代码一步都不上传。
          </p>
          <div className="btn-row">
            <button className="btn" onClick={bind} disabled={busy}>
              {busy ? <span className="spinner" /> : null}
              生成 MCP 绑定凭据
            </button>
          </div>
          {token && (
            <div className="notice ok mt">
              <div className="tiny">把这一行写进你的 MCP 配置（只显示一次）：</div>
              <div className="mono small" style={{ wordBreak: "break-all", marginTop: 6 }}>
                X-Resonance-Peer: {token}
              </div>
            </div>
          )}
          <div className="gap-sm mt">
            {(bindings.data?.bindings ?? []).map((binding: any) => (
              <div key={binding.id} className="row-between">
                <span className="small">
                  {binding.label || binding.kind} · <span className={binding.status === "active" ? "chip lime" : "chip"}>{binding.status}</span>
                </span>
                <span className="tiny muted">{binding.last_seen_at ? timeAgo(binding.last_seen_at) : "未使用"}</span>
              </div>
            ))}
            {(bindings.data?.bindings ?? []).length === 0 && <div className="tiny muted">还没有绑定。</div>}
          </div>
        </Panel>
      </div>

      <Panel title="透明度账本：我们到底存了什么">
        {transparency.loading ? (
          <Spinner />
        ) : (
          <div className="grid grid-2">
            <div>
              <div className="eyebrow">STORED</div>
              <div className="kv mt">
                {(transparency.data?.what_we_store ?? []).map((item: any) => (
                  <div key={item.field} className="kv-row">
                    <div className="kv-k mono">{item.field}</div>
                    <div className="kv-v small">
                      {item.content} {item.encrypted && <span className="chip lime">encrypted</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <div className="eyebrow">NEVER STORED</div>
              <ul className="rule-list mt" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {(transparency.data?.never_stored ?? []).map((item: string) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <div className="eyebrow mt-lg">RETENTION</div>
              <div className="kv mt">
                {Object.entries(transparency.data?.retention ?? {}).map(([key, value]) => (
                  <div key={key} className="kv-row">
                    <div className="kv-k mono">{key}</div>
                    <div className="kv-v small">{String(value)}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </Panel>

      <Panel title="我的审计事件">
        {(transparency.data?.events ?? []).length === 0 ? (
          <Empty>还没有事件。</Empty>
        ) : (
          <div className="term">
            {(transparency.data?.events ?? []).slice(0, 18).map((event: any) => (
              <div key={`${event.at}-${event.kind}`} className="ln">
                <span className="t">{new Date(event.at).toLocaleTimeString()}</span>
                <span>
                  <span className="id">{event.kind}</span>{" "}
                  <span className="kind">
                    {event.meta?.reason ? `· ${event.meta.reason}` : ""} {event.meta?.gate === true ? "· gated" : ""}
                  </span>
                </span>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
