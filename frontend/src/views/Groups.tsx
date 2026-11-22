import { useState } from "react";
import { api } from "../api";
import { Empty, Panel, Spinner, useAsync, useToast } from "../components/ui";

export default function Groups() {
  const toast = useToast();
  const groups = useAsync(() => api.groups(), []);
  const [busy, setBusy] = useState<string | null>(null);

  async function leave(id: string) {
    setBusy(id);
    try {
      await api.leaveGroup(id);
      toast.push("已退出");
      groups.reload();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(null);
    }
  }

  const list = groups.data?.groups ?? [];

  return (
    <div className="grid">
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 04 · MICRO GROUPS</div>
          <h1>三到六人的桌子，比一对一轻松得多</h1>
          <p className="lede">
            顶会里最有效的交流形式不是两个人干坐着，而是四个人围一桌。系统按「角色互补」把算法、系统、评测、理论拼在一起，
            并给每个桌自动生成一份 50 分钟、带三个具体问题的议程——没有主讲，没有 PPT。
          </p>
        </div>
      </div>

      {groups.loading && <Spinner />}
      {!groups.loading && list.length === 0 && (
        <Panel title="我的圆桌">
          <Empty>你还没在任何一个微圆桌里。跑一次暗池之后，系统会按互补关系自动把你排进候选桌。</Empty>
        </Panel>
      )}

      {list.map((group: any) => (
        <Panel
          key={group.id}
          title={
            <span>
              {group.topic} · {group.size} 人 · {group.status}
            </span>
          }
          right={
            <div className="chips">
              <span className="chip cyan">{group.slot?.label}</span>
              <span className="chip">{group.slot?.zone}</span>
            </div>
          }
        >
          <div className="grid grid-2">
            <div>
              <div className="eyebrow">AGENDA · 50 MIN</div>
              <div className="q-list mt">
                {(group.agenda?.questions ?? []).map((question: string) => (
                  <div key={question} className="q-item">
                    {question}
                  </div>
                ))}
              </div>
              <div className="agenda mt">
                {(group.agenda?.timebox ?? []).map((item: any) => (
                  <div key={item.label} className="agenda-item">
                    <span className="agenda-min">{item.minutes}′</span>
                    <span>{item.label}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <div className="eyebrow">桌规</div>
              <ul className="rule-list mt" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {(group.agenda?.rules ?? []).map((rule: string) => (
                  <li key={rule}>{rule}</li>
                ))}
              </ul>
              <div className="eyebrow mt-lg">同桌</div>
              <div className="chips mt">
                {(group.members ?? []).map((member: any) => (
                  <span key={member.id} className="chip cyan">
                    {member.display_name}
                  </span>
                ))}
              </div>
              <div className="chips mt">
                {(group.agenda?.roles ?? []).map((role: string) => (
                  <span key={role} className="chip violet">
                    {role}
                  </span>
                ))}
              </div>
              <div className="btn-row mt">
                <button className="btn btn-ghost btn-sm" onClick={() => leave(group.id)} disabled={busy === group.id}>
                  退出
                </button>
              </div>
            </div>
          </div>
        </Panel>
      ))}
    </div>
  );
}
