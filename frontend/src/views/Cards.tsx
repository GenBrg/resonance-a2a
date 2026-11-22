import { useState } from "react";
import { api } from "../api";
import { Empty, Meter, Panel, Spinner, useAsync, useToast } from "../components/ui";
import type { Card } from "../types";

const KIND_LABEL: Record<string, string> = {
  match: "1v1 · 双盲待解锁",
  group: "微圆桌 · 3–6 人",
  proximity: "近场 · 同一展厅",
  gatekeeper: "门卫 · 结构化申请",
};

export default function Cards() {
  const toast = useToast();
  const cards = useAsync(() => api.cards(), []);
  const [busy, setBusy] = useState<string | null>(null);
  const [slots, setSlots] = useState<Record<string, string>>({});

  async function act(card: Card, action: "accept" | "decline") {
    setBusy(card.id);
    try {
      const result = await api.actCard(card.id, action, slots[card.id]);
      if (action === "accept" && card.kind === "match") {
        if (result.unlock?.accepted_by_both) {
          toast.push("双方都同意了，真名与日程已解锁");
        } else {
          toast.push("你已同意，等对方拍板；在那之前双方都看不到彼此身份");
        }
      } else if (action === "accept") {
        toast.push("已接受");
      } else {
        toast.push("已拒绝，对方不会被告知细节");
      }
      cards.reload();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(null);
    }
  }

  const quota = cards.data?.quota;
  const list: Card[] = cards.data?.cards ?? [];

  return (
    <div className="grid">
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 03 · MORNING CARDS</div>
          <h1>早上八点，最多三张卡</h1>
          <p className="lede">
            没有 99+ 未读。这里只放昨晚通过双盲门槛的连接，每张卡告诉你「为什么值得见」和「第一句话从哪切入」。
            在你点同意、并且对方也点同意之前，两边看到的都只是匿名画像。
          </p>
        </div>
        {quota && (
          <div style={{ minWidth: 190 }}>
            <div className="meter-row">
              <span className="tiny muted">今日额度</span>
              <Meter value={quota.cap ? quota.used / quota.cap : 0} />
              <span className="mono tiny">
                {quota.used}/{quota.cap}
              </span>
            </div>
            <div className="tiny muted right mt">硬上限写在数据库配额表里</div>
          </div>
        )}
      </div>

      {cards.loading && <Spinner />}
      {!cards.loading && list.length === 0 && (
        <Panel title="今日卡片">
          <Empty>
            今天没有卡片。可能是画像池还不够大、你已经用完额度，或者昨晚的单向不达标记录已被静默销毁。
            <br />
            <span className="tiny">单向不合适永远不会通知对方，这是设计，不是故障。</span>
          </Empty>
        </Panel>
      )}

      {list.map((card) => (
        <article key={card.id} className={`ticket ${card.kind}`}>
          <header className="ticket-head">
            <div>
              <div className="ticket-kind">{KIND_LABEL[card.kind] ?? card.kind}</div>
              <div className="ticket-title">{cardTitle(card)}</div>
            </div>
            <div className="chips">
              <span className={`chip ${card.state === "pending" ? "lime" : card.state === "accepted" ? "cyan" : "warn"}`}>
                {card.state}
              </span>
              {card.cohort_size > 0 && <span className="chip">同簇 {card.cohort_size} 人</span>}
            </div>
          </header>
          <div className="ticket-body">
            {card.kind === "match" && <MatchBody card={card} slots={slots} setSlots={setSlots} busy={busy} act={act} />}
            {card.kind === "group" && card.group && <GroupBody card={card} busy={busy} act={act} />}
            {card.kind === "proximity" && <ProximityBody card={card} busy={busy} act={act} />}
            {card.kind === "gatekeeper" && <GatekeeperBody card={card} busy={busy} act={act} />}
            {card.state === "pending" && (
              <div className="btn-row">
                <button className="btn btn-primary btn-sm" onClick={() => act(card, "accept")} disabled={busy === card.id}>
                  {busy === card.id ? <span className="spinner" /> : null}
                  {card.kind === "match" ? "同意解锁身份并约见" : card.kind === "group" ? "加入这张桌子" : "接受"}
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => act(card, "decline")} disabled={busy === card.id}>
                  暂不感兴趣
                </button>
              </div>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

function cardTitle(card: Card): string {
  if (card.kind === "match") {
    const label = card.payload?.shared_labels?.[0] ?? card.payload?.tags?.[0]?.label;
    return label ? `共振 · ${label}` : "双盲待解锁";
  }
  return card.headline;
}

function MatchBody({
  card,
  slots,
  setSlots,
  busy,
  act,
}: {
  card: Card;
  slots: Record<string, string>;
  setSlots: (value: Record<string, string>) => void;
  busy: string | null;
  act: (card: Card, action: "accept" | "decline") => void;
}) {
  const unlock = card.unlock;
  const unlocked = unlock?.state === "unlocked";
  const payload = card.payload ?? {};

  if (unlocked && card.peer) {
    return (
      <div className="gap">
        <div className="notice ok">
          双方都已同意。身份已解锁，日程里已经写好了这一场。
        </div>
        <div className="kv">
          <div className="kv-row">
            <div className="kv-k">对方</div>
            <div className="kv-v">
              <div className="unlocked-name">{card.peer.display_name}</div>
              <div className="small dim">{card.peer.headline}</div>
              <div className="tiny muted">
                {card.peer.affiliation} · {card.peer.venue}
              </div>
            </div>
          </div>
          <div className="kv-row">
            <div className="kv-k">共同方向</div>
            <div className="kv-v">
              <div className="chips">
                {(card.peer.tags ?? []).map((tag) => (
                  <span key={tag} className="chip lime">
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          </div>
          {card.peer.icebreaker && (
            <div className="kv-row">
              <div className="kv-k">第一句</div>
              <div className="kv-v small">{card.peer.icebreaker}</div>
            </div>
          )}
          {card.peer.meeting && (
            <div className="kv-row">
              <div className="kv-k">约定</div>
              <div className="kv-v small">
                {card.peer.meeting.slot} · {card.peer.meeting.zone}
              </div>
            </div>
          )}
          {card.peer.papers && card.peer.papers.length > 0 && (
            <div className="kv-row">
              <div className="kv-k">公开工作</div>
              <div className="kv-v small dim">{(card.peer.papers ?? []).filter(Boolean).join(" / ")}</div>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="gap">
      <div className="why">
        <div className="row">
          <span className="k">为什么</span>
          <span>{payload.reason || "双方 agent 在沙盒里确认了技术互补。"}</span>
        </div>
        <div className="row">
          <span className="k">切入点</span>
          <span>{payload.icebreaker || payload.icebreaker_hint || "从你们共同的卡点切入。"}</span>
        </div>
        {payload.shared_labels?.length > 0 && (
          <div className="row">
            <span className="k">共同方向</span>
            <span className="chips">
              {payload.shared_labels.map((label: string) => (
                <span key={label} className="chip lime">
                  {label}
                </span>
              ))}
            </span>
          </div>
        )}
      </div>

      <div className="locked">
        <span className="seal">SEALED</span>
        <span>
          双方确认前，双方都只能看到匿名画像 · 最低分 {unlock?.min_score ?? payload.min_score ?? "—"} ·{" "}
          {unlock?.accepted_by_me ? "你已同意，等对方" : "尚未解锁"}
        </span>
      </div>

      <div className="form-row">
        <label className="field" style={{ marginBottom: 0 }}>
          <span className="field-label">
            <span>选一个时间</span>
            <span className="hint">双方同意后写入日程</span>
          </span>
          <select
            value={slots[card.id] ?? (payload.slots?.[0]?.id ?? "")}
            onChange={(event) => setSlots({ ...slots, [card.id]: event.target.value })}
          >
            {(payload.slots ?? []).map((slot: any) => (
              <option key={slot.id} value={slot.id}>
                {slot.label} · {slot.zone}
              </option>
            ))}
          </select>
        </label>
        <div className="tiny muted" style={{ alignSelf: "end", paddingBottom: 8 }}>
          {busy === card.id ? "处理中…" : "两张卡都点同意后，身份与日程同时解锁。"}
        </div>
      </div>
    </div>
  );
}

function GroupBody({
  card,
  busy,
  act,
}: {
  card: Card;
  busy: string | null;
  act: (card: Card, action: "accept" | "decline") => void;
}) {
  const group = card.group!;
  return (
    <div className="gap">
      <div className="why">
        <div className="row">
          <span className="k">主题</span>
          <span>
            {group.topic} · {group.size} 人 · {group.slot?.label} @ {group.slot?.zone}
          </span>
        </div>
        <div className="row">
          <span className="k">角色</span>
          <span className="chips">
            {(group.agenda?.roles ?? []).map((role) => (
              <span key={role} className="chip violet">
                {role}
              </span>
            ))}
          </span>
        </div>
      </div>
      <div className="q-list">
        {(group.agenda?.questions ?? []).map((question) => (
          <div key={question} className="q-item">
            {question}
          </div>
        ))}
      </div>
      <div className="agenda">
        {(group.agenda?.timebox ?? []).map((item) => (
          <div key={item.label} className="agenda-item">
            <span className="agenda-min">{item.minutes}′</span>
            <span>{item.label}</span>
          </div>
        ))}
      </div>
      {group.members?.length > 0 ? (
        <div className="chips">
          {group.members.map((member) => (
            <span key={member.id} className="chip cyan">
              {member.display_name}
            </span>
          ))}
        </div>
      ) : (
        <div className="tiny muted">加入后才能看到同桌的人（{group.member_count} 人）。</div>
      )}
      {card.state === "accepted" && (
        <button className="btn btn-ghost btn-sm" onClick={() => act(card, "decline")} disabled={busy === card.id}>
          退出这张桌子
        </button>
      )}
    </div>
  );
}

function ProximityBody({
  card,
  busy,
  act,
}: {
  card: Card;
  busy: string | null;
  act: (card: Card, action: "accept" | "decline") => void;
}) {
  const payload = card.proximity ?? {};
  return (
    <div className="gap">
      <div className="why">
        <div className="row">
          <span className="k">位置</span>
          <span>{payload.session_label ?? payload.session_ref}</span>
        </div>
        <div className="row">
          <span className="k">共同</span>
          <span className="chips">
            {(payload.shared_labels ?? []).map((label: string) => (
              <span key={label} className="chip cyan">
                {label}
              </span>
            ))}
          </span>
        </div>
        <div className="row">
          <span className="k">有效期</span>
          <span>{payload.ttl_minutes ?? 4} 分钟内回应，过期自动失效</span>
        </div>
      </div>
      {card.state === "accepted" && <div className="notice ok">已回应，双方手机上都会出现这次偶遇。</div>}
    </div>
  );
}

function GatekeeperBody({
  card,
  busy,
  act,
}: {
  card: Card;
  busy: string | null;
  act: (card: Card, action: "accept" | "decline") => void;
}) {
  const payload = card.gatekeeper ?? {};
  const pitch = payload.pitch ?? {};
  return (
    <div className="gap">
      <div className="why">
        <div className="row">
          <span className="k">门卫</span>
          <span>
            Tier {payload.tier} · 结构化评分 {payload.score} · <span className="mono">{payload.reason_code}</span>
          </span>
        </div>
        <div className="row">
          <span className="k">凭证</span>
          <span className="chips">
            {(payload.credentials ?? []).length ? (
              payload.credentials.map((label: string) => (
                <span key={label} className="chip orange" style={{ color: "var(--orange)" }}>
                  {label}
                </span>
              ))
            ) : (
              <span className="muted tiny">无验证凭证，仅靠 Pitch 内容过审</span>
            )}
          </span>
        </div>
        <div className="row">
          <span className="k">想要</span>
          <span>{pitch.ask}</span>
        </div>
        <div className="row">
          <span className="k">为什么</span>
          <span>{pitch.why_you}</span>
        </div>
        <div className="row">
          <span className="k">证据</span>
          <span>{pitch.evidence}</span>
        </div>
        <div className="row">
          <span className="k">占用</span>
          <span>{pitch.time_ask}</span>
        </div>
      </div>
      <div className="tiny muted">接受后对方才会看到你的身份；拒绝不会被对方看到具体理由。</div>
    </div>
  );
}


