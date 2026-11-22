"""Micro-group formation.

The brief's iron law: prefer a 3–6 person table over a 1:1 whenever possible. A
table has no host, no audience, and no obligation to perform, which is why it is
the only format that works for people who would otherwise never walk up to a
stranger.

Formation is constrained on purpose:

* **role coverage** — a table needs complementary roles (algorithm, infra, eval,
  theory, product). Four people who all do RLHF get one conversation, then silence.
* **one per affiliation** — two colleagues at the same table turn a mixed group into
  a two-party conversation with spectators.
* **agenda or nothing** — the host artifact is a 50-minute time-boxed agenda with
  three concrete questions, generated from the members' declared bottlenecks.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from app.domain import taxonomy

ROLE_BY_TOPIC: dict[str, str] = {
    "rl_policy_opt": "algo",
    "rl_offline": "algo",
    "rl_env_agent": "algo",
    "rlvr": "algo",
    "reasoning_test_time": "reasoning",
    "long_context": "reasoning",
    "rag": "reasoning",
    "inference_serving": "infra",
    "kv_cache": "infra",
    "distributed_train": "infra",
    "rl_rollout_infra": "infra",
    "kernel_compiler": "infra",
    "hpc_scaling": "infra",
    "edge_ondevice": "infra",
    "quantization": "efficiency",
    "eval_benchmark": "eval",
    "agent_eval": "eval",
    "data_engine": "eval",
    "gen_eval": "eval",
    "optim_theory": "theory",
    "gen_theory": "theory",
    "repr_learning": "theory",
    "alignment": "safety",
    "interpretability": "safety",
    "jailbreak": "safety",
    "privacy_ml": "safety",
    "fairness": "safety",
    "diffusion": "gen",
    "video_gen": "gen",
    "3d_gen": "gen",
    "autoregressive_gen": "gen",
    "vlm": "perception",
    "detection": "perception",
    "embodied": "perception",
    "speech": "perception",
    "multimodal": "perception",
    "protein": "science",
    "materials": "science",
    "physics_sim": "science",
    "weather_climate": "science",
    "bio_genomics": "science",
    "medical_imaging": "science",
    "eeg_bci": "science",
    "recsys": "product",
    "fin_ai": "product",
    "health_app": "product",
    "edu_ai": "product",
    "agent_memory": "product",
    "graphics": "product",
}

ROLE_LABEL = {
    "algo": "算法/训练",
    "reasoning": "推理/评测协议",
    "infra": "系统/推理引擎",
    "efficiency": "压缩/效率",
    "eval": "评测/基准",
    "theory": "理论/分析",
    "safety": "安全/对齐",
    "gen": "生成建模",
    "perception": "感知/多模态",
    "science": "领域科学",
    "product": "产品/落地",
}

MIN_SIZE = 3
MAX_SIZE = 6


@dataclass
class GroupMember:
    user_id: str
    venue: str
    tags: list[str]
    buckets: list[str] = field(default_factory=list)
    domain: str = ""
    bottleneck: str = ""

    def roles(self) -> set[str]:
        return {ROLE_BY_TOPIC[t] for t in self.tags if t in ROLE_BY_TOPIC}

    def umbrellas(self) -> set[str]:
        return {taxonomy.umbrella_of(t) for t in self.tags if t in taxonomy.TOPIC_BY_KEY}


@dataclass
class GroupProposal:
    group_id: str
    topic: str
    topic_key: str
    tags: list[str]
    members: list[str]
    roles: dict[str, list[str]]
    score: float
    agenda: dict[str, Any]

    @property
    def size(self) -> int:
        return len(self.members)


def _pair_affinity(a: GroupMember, b: GroupMember) -> float:
    shared = set(a.tags) & set(b.tags)
    shared_umbrella = a.umbrellas() & b.umbrellas()
    new_roles = (b.roles() - a.roles()) | (a.roles() - b.roles())
    score = 2.0 * len(shared) + 1.2 * len(shared_umbrella) + 1.5 * len(new_roles)
    if a.domain and a.domain == b.domain:
        score -= 4.0  # colleagues at one table kill the format
    if set(a.buckets) & set(b.buckets):
        score += 0.4
    return score


def _dominant_topic(members: list[GroupMember]) -> tuple[str, str]:
    counts: dict[str, int] = {}
    for member in members:
        for tag in member.tags:
            if tag in taxonomy.TOPIC_BY_KEY:
                counts[tag] = counts.get(tag, 0) + 1
    if not counts:
        return "ml_general", "机器学习（泛）"
    key = max(counts, key=lambda k: (counts[k], k))
    return key, taxonomy.TOPIC_BY_KEY[key].label


def build_agenda(topic_key: str, topic_label: str, members: list[GroupMember]) -> dict[str, Any]:
    pains = [m.bottleneck.strip() for m in members if m.bottleneck and len(m.bottleneck.strip()) >= 10][:3]
    questions = [
        f"每人 2 分钟：你最近一个月真正卡住的那个环节是什么（具体到组件，不聊愿景）",
        f"围绕「{topic_label}」：各自踩过最贵的一次坑，以及当时怎么判断该止损",
        "交换一件下次可以互相复用的东西：一条评测口径、一段调度策略，或一个可复现的失败案例",
    ]
    if pains:
        questions.insert(1, f"针对组里提出的卡点，其他人各给一条能立刻试的具体做法（不允许只给方向）")
    return {
        "topic_key": topic_key,
        "topic_label": topic_label,
        "roles": sorted({ROLE_LABEL.get(r, r) for m in members for r in m.roles()}),
        "questions": questions,
        "timebox": [
            {"label": "自我介绍 + 卡点", "minutes": 10},
            {"label": "逐个卡点互评", "minutes": 20},
            {"label": "自由交换", "minutes": 15},
            {"label": "定下一个动作", "minutes": 5},
        ],
        "rules": [
            "没有主讲人，没有 PPT",
            "每个人必须带一个具体的技术卡点来",
            "不得录音、不得引用未公开内容",
        ],
    }


def form_groups(
    members: list[GroupMember],
    *,
    seed_pairs: list[tuple[str, str]] | None = None,
    max_groups: int = 8,
    min_roles: int = 3,
    max_per_domain: int = 1,
) -> list[GroupProposal]:
    """Greedy role-covering cliques, seeded from pairs that already passed the gate."""
    by_id = {m.user_id: m for m in members}
    proposals: list[GroupProposal] = []
    used: set[str] = set()

    seeds = [tuple(p) for p in (seed_pairs or []) if all(x in by_id for x in p)]

    def try_extend(base: list[GroupMember]) -> list[GroupMember] | None:
        group = list(base)
        domains = {m.domain for m in group if m.domain}
        while len(group) < MAX_SIZE:
            best: tuple[float, GroupMember] | None = None
            for candidate in members:
                if candidate.user_id in {m.user_id for m in group} or candidate.user_id in used:
                    continue
                if candidate.domain and candidate.domain in domains and max_per_domain == 1:
                    continue
                if candidate.venue != group[0].venue:
                    continue
                gain = sum(_pair_affinity(m, candidate) for m in group) / len(group)
                if len(candidate.roles() - {r for m in group for r in m.roles()}) == 0:
                    gain -= 1.0
                if best is None or gain > best[0]:
                    best = (gain, candidate)
            if best is None or best[0] <= 0.4 or len(group) >= MAX_SIZE:
                break
            group.append(best[1])
            domains.add(best[1].domain)
            if len(group) >= MIN_SIZE and len({r for m in group for r in m.roles()}) >= min_roles and len(group) >= 4:
                break
        roles = {r for m in group for r in m.roles()}
        if len(group) < MIN_SIZE or len(roles) < min_roles:
            return None
        return group

    for pair in seeds:
        if len(proposals) >= max_groups:
            break
        if any(x in used for x in pair):
            continue
        group = try_extend([by_id[pair[0]], by_id[pair[1]]])
        if not group:
            continue
        proposals.append(_finalise(group))
        used.update(m.user_id for m in group)

    if len(proposals) < max_groups:
        remaining = [m for m in members if m.user_id not in used]
        while len(proposals) < max_groups and len(remaining) >= MIN_SIZE:
            remaining.sort(key=lambda m: (-len(m.tags), m.user_id))
            pivot = remaining[0]
            cohort = [pivot] + [m for m in remaining[1:] if _pair_affinity(pivot, m) > 1.0][: MAX_SIZE - 1]
            group = try_extend(cohort[:2]) if len(cohort) >= 2 else None
            if not group:
                remaining.pop(0)
                continue
            proposals.append(_finalise(group))
            used.update(m.user_id for m in group)
            remaining = [m for m in remaining if m.user_id not in used]

    return proposals


def _finalise(group: list[GroupMember]) -> GroupProposal:
    topic_key, topic_label = _dominant_topic(group)
    tags = sorted({t for m in group for t in m.tags})
    roles: dict[str, list[str]] = {}
    for member in group:
        for role in member.roles():
            roles.setdefault(role, []).append(member.user_id)
    base = sum(_pair_affinity(a, b) for i, a in enumerate(group) for b in group[i + 1 :])
    score = round(base / max(1, len(group) * (len(group) - 1) / 2) + 0.8 * len(roles), 3)
    digest = hashlib.sha256("|".join(sorted(m.user_id for m in group)).encode()).hexdigest()[:10]
    return GroupProposal(
        group_id=f"grp_{digest}",
        topic=topic_label,
        topic_key=topic_key,
        tags=tags,
        members=[m.user_id for m in group],
        roles={k: sorted(v) for k, v in roles.items()},
        score=score,
        agenda=build_agenda(topic_key, topic_label, group),
    )

