import { useEffect, useState } from "react";
import { api } from "../api";
import { Empty, Panel, Spinner, useAsync, useToast } from "../components/ui";
import type { Radar } from "../types";

export default function Proximity() {
  const toast = useToast();
  const venues = useAsync(() => api.proximityVenues(), []);
  const [sessionRef, setSessionRef] = useState("poster-2b");
  const [visible, setVisible] = useState(true);
  const [radar, setRadar] = useState<Radar | null>(null);
  const [beacon, setBeacon] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  async function checkin() {
    setBusy(true);
    try {
      const result = await api.checkin(sessionRef, visible);
      setBeacon(result);
      toast.push("已签到，信标 15 分钟后自动轮换");
      await scan();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function scan() {
    try {
      const result = await api.radar();
      setRadar(result);
    } catch (error) {
      toast.push((error as Error).message, "err");
    }
  }

  async function checkout() {
    await api.checkout();
    setRadar(null);
    setBeacon(null);
    toast.push("已离场，附近信号立即失效");
  }

  async function ping(ref: string) {
    try {
      const result = await api.ping(ref);
      toast.push(result.status === "pinged" ? "已打招呼，对方 4 分钟内可回应" : "已经打过招呼了");
    } catch (error) {
      toast.push((error as Error).message, "err");
    }
  }

  useEffect(() => {
    api.radar().then(setRadar).catch(() => undefined);
  }, []);

  const sessionsList = venues.data?.sessions ?? [];

  return (
    <div className="grid">
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 05 · PROXIMITY</div>
          <h1>擦肩而过，变成顺路一杯咖啡</h1>
          <p className="lede">
            线上匹配解决「该见谁」，解决不了「谁现在就在二十米外」。签到后系统发一个 15 分钟轮换的匿名信标，
            只告诉你同一展厅里存在共同方向的人——不给距离、不给方向、不给姓名。离场即失效。
          </p>
        </div>
        <div className="chips">
          <span className="chip cyan">window 15 min</span>
          <span className="chip">no distance · no direction</span>
        </div>
      </div>

      <div className="grid grid-rail">
        <Panel title="签到到会场区域">
          <div className="grid grid-2">
            <label className="field">
              <span className="field-label">你在哪</span>
              <select value={sessionRef} onChange={(event) => setSessionRef(event.target.value)}>
                {sessionsList.map((item: any) => (
                  <option key={item.ref} value={item.ref}>
                    {item.label} · {item.zone}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">可见性</span>
              <select value={visible ? "1" : "0"} onChange={(event) => setVisible(event.target.value === "1")}>
                <option value="1">同好可见（推荐）</option>
                <option value="0">隐身，只看不发</option>
              </select>
            </label>
          </div>
          <div className="btn-row">
            <button className="btn btn-primary" onClick={checkin} disabled={busy}>
              {busy ? <span className="spinner" /> : null}
              签到我在这里
            </button>
            <button className="btn" onClick={scan}>
              刷新雷达
            </button>
            <button className="btn btn-ghost" onClick={checkout}>
              离场
            </button>
          </div>
          {beacon && (
            <div className="mt">
              <div className="kv-row">
                <div className="kv-k">匿名信标</div>
                <div className="kv-v mono small">{beacon.beacon}</div>
              </div>
              <div className="kv-row">
                <div className="kv-k">失效时间</div>
                <div className="kv-v small">{new Date(beacon.expires_at).toLocaleTimeString()}</div>
              </div>
            </div>
          )}
        </Panel>

        <Panel title="附近雷达">
          <div style={{ display: "grid", placeItems: "center", padding: "10px 0 18px" }}>
            <div className="beacon">{radar?.peer_count ?? 0} 人</div>
          </div>
          {!radar?.active && <div className="tiny muted">先签到，雷达才会告诉你附近有谁。</div>}
          {radar?.active && (radar.peers ?? []).length === 0 && (
            <Empty>此刻同一区域没有共同方向的人。展厅大了，这个结果很正常。</Empty>
          )}
          <div className="gap-sm">
            {(radar?.peers ?? []).map((peer) => (
              <div key={peer.ref} className="row-between">
                <div>
                  <div className="small">{peer.alias}</div>
                  <div className="chips mt">
                    {(peer.shared_tags ?? []).map((tag) => (
                      <span key={tag} className="chip cyan">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
                <button className="btn btn-sm" onClick={() => ping(peer.ref)}>
                  打个招呼
                </button>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel title="这个功能的边界（写进代码的）">
        <ul className="rule-list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          <li>信标是 HMAC(venue, session, 时间桶, 你自己的 id)，别人截屏转发无效。</li>
          <li>雷达只返回匿名代号与泛化标签，永远不返回姓名、机构、距离或方向。</li>
          <li>打招呼要消耗对方今日卡片额度，且只有对方点接受，你才会得到回应。</li>
          <li>签到行是单条的，重新签到会覆盖旧行；离场直接删除，不留历史轨迹。</li>
        </ul>
      </Panel>
    </div>
  );
}
