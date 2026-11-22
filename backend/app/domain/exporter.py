"""Post-conference export.

The brief's answer to the "seven-day disposable agent" objection: the seven days
must leave the attendee with something that survives the flight home. Two artifacts,
both plain files, both theirs:

* a Markdown dossier — who they met, why it was a match, the icebreaker that
  worked, the micro-groups they sat in, the agenda, and the calibrated preference
  summary their harness can eat;
* an ``AgentCard`` JSON — the same identity/provenance in the shape an A2A peer
  understands, with this platform's extension namespace.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

EXTENSION_URI = "https://resonance.conference/a2a/ext/v1"
SKILL_ID = "conference_match"


def _fmt_day(value: dt.datetime | dt.date | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d")


def build_markdown(
    *,
    user: dict[str, Any],
    tags: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    preferences: dict[str, Any],
    stats: dict[str, Any],
    generated_at: dt.datetime | None = None,
) -> str:
    now = generated_at or dt.datetime.now(dt.timezone.utc)
    lines: list[str] = []
    lines.append(f"# NeurIPS 2026 · {user.get('display_name', '参会者')} 的连接纪要")
    lines.append("")
    lines.append(f"> 导出时间：{now.strftime('%Y-%m-%d %H:%M UTC')} · 场馆：{user.get('venue', '-')}")
    lines.append("")
    lines.append("这七天你的 agent 在封闭沙盒里跑过的每一次试探都是阅后即焚的，")
    lines.append("下面留下的是**已经双向确认**的结果，以及你自己的偏好校准。可以直接喂回你日常用的 AI。")
    lines.append("")

    lines.append("## 一、你的公开画像（已做 k-匿名泛化）")
    lines.append("")
    if tags:
        for tag in tags:
            lines.append(f"- {tag.get('label', tag.get('tag'))}  `level L{tag.get('level', 1)}`")
    else:
        lines.append("- （尚未构建）")
    lines.append("")

    lines.append("## 二、双向确认过的连接")
    lines.append("")
    if not matches:
        lines.append("本次会期没有通过双盲门槛的连接。单方面未达标的记录已被销毁，对方从未知道你的存在。")
    for item in matches:
        lines.append(f"### {item.get('peer_name', '对方')} · {item.get('state_label', '')}")
        lines.append("")
        if item.get("peer_headline"):
            lines.append(f"- 一句话：{item['peer_headline']}")
        if item.get("peer_tags"):
            lines.append(f"- 共同方向：{'、'.join(item['peer_tags'])}")
        if item.get("reason"):
            lines.append(f"- 为什么值得见：{item['reason']}")
        if item.get("icebreaker"):
            lines.append(f"- 破冰切入点：{item['icebreaker']}")
        if item.get("meeting"):
            lines.append(f"- 约定：{item['meeting']}")
        lines.append("")

    lines.append("## 三、学术微组局")
    lines.append("")
    if not groups:
        lines.append("（本次未入组）")
    for group in groups:
        lines.append(f"### {group.get('topic', '圆桌')} · {group.get('slot', '')}")
        lines.append("")
        lines.append(f"- 成员：{'、'.join(group.get('member_names', []))}")
        agenda = group.get("agenda", {}) or {}
        for question in agenda.get("questions", []):
            lines.append(f"- 议题：{question}")
        for item in agenda.get("timebox", []):
            lines.append(f"  - {item.get('minutes')} 分钟 · {item.get('label')}")
        lines.append("")

    lines.append("## 四、偏好校准（给下一个 agent 用）")
    lines.append("")
    for key, value in preferences.items():
        lines.append(f"- **{key}**：{value}")
    lines.append("")

    lines.append("## 五、本次隐私账本")
    lines.append("")
    lines.append(f"- 沙盒场次：{stats.get('sessions', 0)}")
    lines.append(f"- 通过双盲门槛：{stats.get('gated', 0)}")
    lines.append(f"- 因单向不达标静默销毁：{stats.get('burned_onesided', 0)}")
    lines.append(f"- 被判定为恶意注入而中止：{stats.get('burned_injection', 0)}")
    lines.append(f"- 原始对话留存：0 条（结构上不落盘）")
    lines.append("")
    return "\n".join(lines)


def build_agent_card(
    *,
    user: dict[str, Any],
    tags: list[dict[str, Any]],
    credentials: list[dict[str, Any]],
    endpoint: str,
    skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    capabilities = {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": True,
    }
    card: dict[str, Any] = {
        "protocolVersion": "0.2.5",
        "name": f"resonance-{user.get('id', 'attendee')}",
        "description": (
            "Resonance attendee relay agent. Handles double-blind conference matchmaking. "
            "It will not disclose private intent, transcripts, or identity before mutual unlock."
        ),
        "url": endpoint,
        "preferredTransport": "JSONRPC",
        "version": "1.0.0",
        "capabilities": capabilities,
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": skills
        or [
            {
                "id": SKILL_ID,
                "name": "Conference matchmaking (double-blind)",
                "description": "Submit a structured intent or pitch; receive a gate decision. Never returns profiles.",
                "tags": ["conference", "matchmaking", "privacy"],
                "examples": ["I am hiring an inference-systems engineer; is this attendee open to a 15-minute chat?"],
            }
        ],
        "extensions": [
            {
                "uri": EXTENSION_URI,
                "description": "Resonance double-blind matchmaking extension",
                "required": False,
                "params": {
                    "venue": user.get("venue", ""),
                    "generalized_tags": [t.get("tag") for t in tags],
                    "k_anonymity": {"k": 10, "cohort_key": user.get("cohort_key", "")},
                    "credential_summary": [
                        {"kind": c.get("kind"), "status": c.get("status")} for c in credentials
                    ],
                    "transcript_policy": "never_persisted",
                    "attention_budget": {"cards_per_day": 3},
                },
            }
        ],
        "securitySchemes": {
            "bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
        },
        "security": [{"bearer": []}],
    }
    return card


def dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
