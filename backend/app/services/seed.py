"""Demo population.

240 attendees with weighted topic mixes, so k-anonymity is exercised for real
(cohorts actually reach 10 rather than everything collapsing to one umbrella), plus
a handful of story personas that make the product legible in a single screenshot.

Everything is deterministic: same seed, same population, same match run. That
matters for demos and for tests that need a stable pool.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.logging import log
from app.db.models import Credential, GatekeeperPolicy, GeneralProfile, User
from app.domain import gatekeeper as gk_domain
from app.domain import taxonomy
from app.services import profiles as profile_service

RNG_SEED = 20261208
DEMO_PASSWORD = "demo1234"

INSTITUTIONS: list[tuple[str, str]] = [
    ("Singapore AI Lab", "singapore-ailab.ai"),
    ("ETH Zurich", "ethz.ch"),
    ("UC Berkeley", "berkeley.edu"),
    ("Tsinghua University", "tsinghua.edu.cn"),
    ("Peking University", "pku.edu.cn"),
    ("Stanford University", "stanford.edu"),
    ("MIT CSAIL", "mit.edu"),
    ("Carnegie Mellon University", "cmu.edu"),
    ("University of Oxford", "ox.ac.uk"),
    ("University of Cambridge", "cam.ac.uk"),
    ("EPFL", "epfl.ch"),
    ("National University of Singapore", "nus.edu.sg"),
    ("NTU", "ntu.edu.sg"),
    ("HKUST", "ust.hk"),
    ("Shanghai Jiao Tong University", "sjtu.edu.cn"),
    ("Fudan University", "fudan.edu.cn"),
    ("Zhejiang University", "zju.edu.cn"),
    ("USTC", "ustc.edu.cn"),
    ("DeepSeek", "deepseek.com"),
    ("Alibaba Qwen", "alibaba-inc.com"),
    ("ByteDance Seed", "bytedance.com"),
    ("Moonshot AI", "moonshot.cn"),
    ("StepFun", "stepfun.com"),
    ("MiniMax", "minimax.io"),
    ("Microsoft Research", "microsoft.com"),
    ("Google DeepMind", "deepmind.com"),
    ("Meta FAIR", "meta.com"),
    ("NVIDIA Research", "nvidia.com"),
    ("Northline Capital", "northline.vc"),
    ("Ant Group", "antgroup.com"),
]

SURNAMES = ["陈", "王", "李", "张", "刘", "赵", "周", "吴", "徐", "孙", "郑", "何", "郭", "林", "程", "高"]
GIVEN = ["哲", "驰", "琳", "泽", "涵", "宇", "欣然", "亦凡", "wissam", "一鸣", "思远", "楚", "嘉铭", "开", "子豪", "沐"]
ENGLISH_FIRST = ["Maya", "Aiden", "Sofia", "Noah", "Lena", "Kai", "Iris", "Adam", "Nora", "Elias", "Tara", "Jonas", "Mira", "Leo"]
ENGLISH_LAST = ["Keller", "Park", "Rossi", "Berg", "Novak", "Tanaka", "Silva", "Fischer", "Haddad", "Lindqvist", "Mehta", "Osei"]

TOPIC_WEIGHTS: dict[str, int] = {
    "inference_serving": 9,
    "kv_cache": 8,
    "rl_rollout_infra": 7,
    "rl_policy_opt": 9,
    "rlvr": 8,
    "reasoning_test_time": 9,
    "long_context": 8,
    "distributed_train": 6,
    "quantization": 6,
    "diffusion": 8,
    "video_gen": 6,
    "vlm": 8,
    "eval_benchmark": 7,
    "alignment": 7,
    "interpretability": 6,
    "privacy_ml": 5,
    "jailbreak": 5,
    "agent_memory": 7,
    "agent_eval": 5,
    "rag": 5,
    "protein": 4,
    "materials": 3,
    "physics_sim": 3,
    "embodied": 4,
    "speech": 4,
    "recsys": 4,
    "data_engine": 5,
    "optim_theory": 4,
    "continual": 3,
    "edge_ondevice": 4,
    "kernel_compiler": 4,
    "hpc_scaling": 3,
}

BOTTLENECKS: dict[str, list[str]] = {
    "inference_serving": [
        "请求长度方差太大，批处理一开就拖尾，P99 比 P50 差三个数量级",
        "冷启动那段权重加载把首 token 延迟拉到秒级，缓存命中率上不去",
    ],
    "kv_cache": [
        "多机 rollout 阶段 KV cache 换入换出把长尾延迟顶起来了，显存碎片比总量更致命",
        "前缀缓存命中率看着高，但页表回收时机不对，峰值显存还是会翻倍",
    ],
    "rl_rollout_infra": [
        "训练和采样之间是同步的，GPU 有一半时间在等人，异步之后 off-policy 偏差又压不住",
        "权重热更新时显存峰值飙到两倍，只能靠停等规避，吞吐掉一半",
    ],
    "rl_policy_opt": [
        "奖励尺度一变策略就崩，KL 约束调到能训但学不动，中间没有可行区",
        "长序列 credit assignment 完全没有信号，只能靠过程奖励硬凑",
    ],
    "rlvr": [
        "可验证奖励被模型摸到了漏洞，通过率上去了但真实能力没动",
        "单测覆盖率不足，模型学到的其实是格式而不是推理",
    ],
    "reasoning_test_time": [
        "搜索宽度一加，延迟和成本线性涨，但准确率是亚线性的",
        "验证器本身有偏，把模型带向它擅长而不是正确的那条路",
    ],
    "long_context": [
        "上下文一长注意力就被稀释，中间段落基本被忽略",
        "检索质量没问题，但拼装顺序一换结果就变，没法归因",
    ],
    "diffusion": [
        "少步数采样已经把质量压到极限了，再往下掉细节",
        "蒸馏后多样性塌了，同一 prompt 出图风格高度趋同",
    ],
    "vlm": [
        "视觉特征被语言侧过早压制，细粒度 grounding 一直上不去",
        "高分辨率切 patch 以后 token 数爆炸，推理成本兜不住",
    ],
    "eval_benchmark": [
        "评测污染查不出来，改个括号码就涨两个点",
        "人工评测的一致性太差，两个标注员的相关只有 0.4",
    ],
    "alignment": [
        "拒答率调上去了有用性就掉，两者没有可用的帕累托前沿",
        "偏好数据里标注者的口味差异比模型差异还大",
    ],
    "distributed_train": [
        "跨机 all-reduce 占了大头，拓扑重排后单卡利用率反而波动",
        "checkpoint 写盘的时候整个训练停十几秒，一天下来说几十次",
    ],
    "agent_memory": [
        "长程任务做到三十步以后就开始丢状态，工具返回的噪声把它带偏",
        "记忆写入没有衰减策略，越用越慢",
    ],
    "privacy_ml": [
        "差分隐私的噪声一加，下游任务指标直接腰斩",
        "k-匿名在特征组合上根本不成立，实际能反推到个位数人群",
    ],
    "interpretability": [
        "机制解释做出来了，但换个随机种子电路就变了，没法验证",
        "probe 的分类准确率高，但那可能只是它在读标签泄漏",
    ],
}

CONTRARIAN: dict[str, list[str]] = {
    "inference_serving": ["把调度器当配角是错的，同等模型下调度能拉开一倍吞吐"],
    "kv_cache": ["继续堆显存没用，关键是换入换出的时机而不是容量"],
    "rl_rollout_infra": ["端到端异步是伪命题，必须先接受一段可控分布的偏差"],
    "rl_policy_opt": ["奖励塑形基本是反模式，不如把力气花在数据配比上"],
    "rlvr": ["可验证奖励的瓶颈不是覆盖度，而是验证器本身的偏差"],
    "reasoning_test_time": ["测试时计算被高估了，长思考更多是在自我纠错而不是探索"],
    "long_context": ["百万上下文是营销数字，真正的问题是注意力分配而不是窗口长度"],
    "diffusion": ["步数蒸馏已经触顶，下一步一定在求解器而不是网络结构"],
    "vlm": ["视觉编码器再强也没用，语言侧的归纳偏置才是瓶颈"],
    "eval_benchmark": ["榜单已经失去区分度，真正该看的是失败样本的分布"],
    "alignment": ["对齐不是价值观问题，是标注协议问题"],
    "distributed_train": ["通信优化做到后面是拓扑问题，不是算法问题"],
    "agent_memory": ["记忆检索的召回率不是关键，写入时的抽象层级才是"],
    "privacy_ml": ["隐私和效用不是权衡关系，很多场景下约束反而逼出更好的表示"],
    "interpretability": ["可解释性现在缺的不是方法，是一套能被证伪的评估口径"],
}

INTENT_WEIGHTS: dict[str, int] = {
    "research_collab": 24,
    "peer_friendship": 14,
    "research_feedback": 12,
    "job_quiet": 12,
    "hiring_research": 7,
    "hiring_infra": 4,
    "cofounder_infra": 4,
    "cofounder_algo": 4,
    "job_intern": 6,
    "investing": 3,
    "funding_raising": 3,
    "hiring_intern": 2,
    "cofounder_product": 2,
}

STORY: list[dict[str, Any]] = [
    {
        "key": "chen",
        "display_name": "陈哲",
        "venue": "sydney",
        "affiliation": "Singapore AI Lab",
        "domain": "singapore-ailab.ai",
        "headline": "RL 算法研究员，正在找推理系统方向的联创",
        "topics": ["rl_policy_opt", "rlvr", "rl_rollout_infra"],
        "papers": [("RLVR 的奖励漏洞与过程奖励的替代路径", "rlvr"), ("异步 rollout 中的策略偏差校正", "rl_rollout_infra")],
        "intents": ["cofounder_infra", "job_quiet"],
        "bottleneck": "千卡级异步 rollout 阶段 KV cache 换入换出把长尾延迟顶到不可接受，显存碎片比容量更致命",
        "contrarian": "纯靠合成数据堆长度已经见顶，关键在动态剪枝与调度而不是更多 token",
        "deal_breakers": ["ppt_only", "no_oss_record"],
        "avoid_domains": ["singapore-ailab.ai"],
        "credentials": [("orcid", True), ("github", True)],
    },
    {
        "key": "maya",
        "display_name": "Maya Keller",
        "venue": "sydney",
        "affiliation": "ETH Zurich",
        "domain": "ethz.ch",
        "headline": "PhD 四年级，开源推理引擎作者，想找算法向联创",
        "topics": ["inference_serving", "kv_cache", "kernel_compiler"],
        "papers": [("分层分页调度在长尾延迟上的实测", "kv_cache")],
        "intents": ["cofounder_algo"],
        "bottleneck": "分层分页调度压掉了尾延迟，但权重热更新时的显存峰值仍然要靠停等规避",
        "contrarian": "调度器不是配角，同等模型下调度能拉开一倍吞吐差距",
        "deal_breakers": ["no_scale", "pure_theory"],
        "avoid_domains": [],
        "credentials": [("orcid", True), ("github", True), ("institutional_email", True)],
    },
    {
        "key": "wanglin",
        "display_name": "王琳",
        "venue": "sydney",
        "affiliation": "UC Berkeley",
        "domain": "berkeley.edu",
        "headline": "通信优化方向，关注大规模训练的网络瓶颈",
        "topics": ["distributed_train", "hpc_scaling", "rl_rollout_infra"],
        "papers": [("拓扑感知的 all-reduce 重排", "distributed_train")],
        "intents": ["research_collab"],
        "bottleneck": "跨机通信占了大头，拓扑重排后单卡利用率波动反而更大",
        "contrarian": "通信优化做到后面是拓扑问题，不是算法问题",
        "deal_breakers": ["credential_showoff"],
        "avoid_domains": [],
        "credentials": [("orcid", True), ("institutional_email", True)],
    },
    {
        "key": "lize",
        "display_name": "李泽",
        "venue": "sydney",
        "affiliation": "Tsinghua University",
        "domain": "tsinghua.edu.cn",
        "headline": "评测基准与污染检测",
        "topics": ["eval_benchmark", "data_engine", "agent_eval"],
        "papers": [("隐式污染的检测口径", "eval_benchmark")],
        "intents": ["research_collab", "peer_friendship"],
        "bottleneck": "隐式污染查不出来，改个括号码就涨两个点",
        "contrarian": "榜单已经失去区分度，真正该看的是失败样本的分布",
        "deal_breakers": ["hype_only"],
        "avoid_domains": [],
        "credentials": [("orcid", True)],
    },
    {
        "key": "aiden",
        "display_name": "Aiden Park",
        "venue": "sydney",
        "affiliation": "ByteDance Seed",
        "domain": "bytedance.com",
        "headline": "Seed 推理基础设施负责人，在招人",
        "topics": ["inference_serving", "rl_rollout_infra", "quantization"],
        "papers": [("生产环境下的批处理与尾延迟控制", "inference_serving")],
        "intents": ["hiring_infra", "hiring_research"],
        "bottleneck": "线上 P99 被超长请求拖死，分桶以后吞吐上限又掉",
        "contrarian": "推理服务的公平性比峰值吞吐更重要",
        "deal_breakers": ["pure_theory", "no_oss_record"],
        "avoid_domains": [],
        "credentials": [("institutional_email", True), ("github", True)],
        "gatekeeper": True,
    },
    {
        "key": "sofia",
        "display_name": "Sofia Rossi",
        "venue": "sydney",
        "affiliation": "Google DeepMind",
        "domain": "deepmind.com",
        "headline": "研究科学家，机制可解释性，招研究员",
        "topics": ["interpretability", "alignment", "optim_theory"],
        "papers": [("跨规模一致性的机制验证", "interpretability")],
        "intents": ["hiring_research", "research_collab"],
        "bottleneck": "机制解释换个随机种子就变，缺可证伪的评估口径",
        "contrarian": "可解释性现在缺的不是方法，是一套能被证伪的评估口径",
        "deal_breakers": ["hype_only", "no_commitment"],
        "avoid_domains": [],
        "credentials": [("orcid", True), ("institutional_email", True), ("openreview", True)],
        "gatekeeper": True,
    },
    {
        "key": "noah",
        "display_name": "Noah Berg",
        "venue": "sydney",
        "affiliation": "Northline Capital",
        "domain": "northline.vc",
        "headline": "早期投资，只看推理与 agent 基础设施",
        "topics": ["inference_serving", "agent_memory", "recsys"],
        "papers": [],
        "intents": ["investing"],
        "bottleneck": "看了一堆 PPT 融资，真正碰过千卡集群的团队太少",
        "contrarian": "基础设施的护城河在交付速度，不在论文数量",
        "deal_breakers": ["ppt_only", "hype_only"],
        "avoid_domains": [],
        "credentials": [("institutional_email", True)],
        "gatekeeper": True,
    },
    {
        "key": "zhanghan",
        "display_name": "张涵",
        "venue": "sydney",
        "affiliation": "Shanghai Jiao Tong University",
        "domain": "sjtu.edu.cn",
        "headline": "硕士二年级，做 RL 训练稳定性，想找实习",
        "topics": ["rl_policy_opt", "continual", "rlvr"],
        "papers": [("奖励尺度对策略崩坏的敏感性", "rl_policy_opt")],
        "intents": ["job_intern", "peer_friendship"],
        "bottleneck": "奖励尺度一变策略就崩，中间没有可行区",
        "contrarian": "奖励塑形基本是反模式，不如把力气花在数据配比上",
        "deal_breakers": ["recruiter_spam"],
        "avoid_domains": [],
        "credentials": [("github", True)],
    },
    {
        "key": "zhengwei",
        "display_name": "郑伟",
        "venue": "sydney",
        "affiliation": "Singapore AI Lab",
        "domain": "singapore-ailab.ai",
        "headline": "同实验室的系统方向研究员",
        "topics": ["inference_serving", "kv_cache", "distributed_train"],
        "papers": [("训练推理协同的调度接口", "kv_cache")],
        "intents": ["research_collab", "hiring_infra"],
        "bottleneck": "训练和推理共用集群时优先级调度没法兼顾两边 SLO",
        "contrarian": "训练推理协同的收益被高估了，隔离部署往往更省心",
        "deal_breakers": ["no_commitment"],
        "avoid_domains": [],
        "credentials": [("institutional_email", True)],
    },
]


# ---------------------------------------------------------------------------
def _weighted_choice(rng: random.Random, weights: dict[str, int], k: int) -> list[str]:
    keys = list(weights)
    picks: list[str] = []
    pool = list(keys)
    while len(picks) < k and pool:
        weights_now = [weights[key] for key in pool]
        chosen = rng.choices(pool, weights=weights_now, k=1)[0]
        picks.append(chosen)
        pool.remove(chosen)
    return picks


def _orcid(rng: random.Random) -> str:
    digits = [str(rng.randint(0, 9)) for _ in range(15)]
    total = 0
    for ch in digits:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    digits.append("X" if check == 10 else str(check))
    raw = "".join(digits)
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


def _arxiv(rng: random.Random, used: set[str]) -> str:
    for _ in range(20):
        candidate = f"26{rng.randint(1, 9):02d}.{rng.randint(10000, 99999)}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    return f"2699.{len(used):05d}"


def _person(rng: random.Random, index: int) -> tuple[str, str, str]:
    if rng.random() < 0.45:
        first = rng.choice(ENGLISH_FIRST)
        last = rng.choice(ENGLISH_LAST)
        name = f"{first} {last}"
        slug = f"{first.lower()}.{last.lower()}"
    else:
        name = rng.choice(SURNAMES) + rng.choice(GIVEN)
        slug = f"user{index:03d}"
    return name, f"{slug}.{index:03d}@attendee.demo", slug


async def seed(session: AsyncSession, *, population: int = 240) -> dict[str, Any]:
    existing = (await session.execute(select(func.count(User.id)))).scalar() or 0
    if existing:
        return {"skipped": True, "users": int(existing)}

    rng = random.Random(RNG_SEED)
    created: list[User] = []
    used_arxiv: set[str] = set()

    async def add_user(
        *,
        email: str,
        display_name: str,
        venue: str,
        affiliation: str,
        domain: str,
        headline: str,
        topics: list[str],
        papers: list[tuple[str, str]],
        intents: list[str],
        bottleneck: str,
        contrarian: str,
        deal_breakers: list[str],
        avoid_domains: list[str],
        credentials: list[tuple[str, bool]],
        gatekeeper: bool = False,
        role: str = "attendee",
    ) -> User:
        user = User(
            email=email,
            password_hash=security.hash_password(DEMO_PASSWORD),
            display_name=display_name,
            venue=venue,
            affiliation=affiliation,
            affiliation_domain=domain,
            headline=headline,
            role=role,
        )
        session.add(user)
        await session.flush()
        paper_rows = [
            {
                "id": _arxiv(rng, used_arxiv),
                "title": title,
                "topics": [topic],
                "url": f"https://arxiv.org/abs/{_arxiv(rng, used_arxiv)}",
            }
            for title, topic in papers
        ]
        public = await profile_service.save_public_anchor(
            session,
            user,
            papers=paper_rows,
            topics_raw=[taxonomy.TOPIC_BY_KEY[t].label for t in topics if t in taxonomy.TOPIC_BY_KEY],
            github_handle=f"{email.split('@')[0].split('.')[0]}-gh" if any(k == "github" for k, _ in credentials) else "",
            ingest_source="seed",
        )
        forbidden = profile_service.forbidden_terms(user, public)
        payload = profile_service.build_vault_from_intake(
            user=user,
            data={
                "bottleneck": bottleneck,
                "contrarian": contrarian,
                "intents": intents,
                "deal_breakers": deal_breakers,
                "avoid_domains": avoid_domains,
                "topics": [taxonomy.TOPIC_BY_KEY[t].label for t in topics if t in taxonomy.TOPIC_BY_KEY],
                "sandbox_consent": True,
            },
            source="seed",
            forbidden_terms=forbidden,
        )
        await profile_service.save_vault(session, user, payload, source="seed")
        for kind, verified in credentials:
            subject = {
                "orcid": _orcid(rng),
                "github": f"{email.split('@')[0].split('.')[0]}-gh",
                "institutional_email": email,
                "doi": f"10.48550/arxiv.{_arxiv(rng, used_arxiv)}",
                "openreview": f"~{display_name.replace(' ', '_')}1",
                "arxiv": _arxiv(rng, used_arxiv),
            }.get(kind, "")
            if not subject:
                continue
            session.add(
                Credential(
                    user_id=user.id,
                    kind=kind,
                    subject=subject,
                    issuer="seed",
                    status="verified" if verified else "unverified",
                    verified_at=dt.datetime.now(dt.timezone.utc) if verified else None,
                )
            )
        if gatekeeper:
            session.add(
                GatekeeperPolicy(
                    user_id=user.id,
                    enabled=True,
                    rules={
                        **gk_domain.DEFAULT_POLICY,
                        "fairness": {"stranger_slots_per_day": 1},
                    },
                    stranger_slots_per_day=1,
                    pitch_threshold=70,
                )
            )
        created.append(user)
        return user

    # ---- story personas -------------------------------------------------
    for spec in STORY:
        await add_user(
            email=f"{spec['key']}@demo.resonance",
            display_name=spec["display_name"],
            venue=spec["venue"],
            affiliation=spec["affiliation"],
            domain=spec["domain"],
            headline=spec["headline"],
            topics=spec["topics"],
            papers=spec["papers"],
            intents=spec["intents"],
            bottleneck=spec["bottleneck"],
            contrarian=spec["contrarian"],
            deal_breakers=spec["deal_breakers"],
            avoid_domains=spec["avoid_domains"],
            credentials=spec["credentials"],
            gatekeeper=spec.get("gatekeeper", False),
        )

    # ---- admin ----------------------------------------------------------
    admin = User(
        email="admin@resonance.dev",
        password_hash=security.hash_password(DEMO_PASSWORD),
        display_name="Ops",
        venue="sydney",
        affiliation="Resonance",
        affiliation_domain="resonance.dev",
        headline="平台运维",
        role="admin",
    )
    session.add(admin)
    await session.flush()

    # ---- population -----------------------------------------------------
    remaining = max(0, population - len(created))
    venue_plan = (
        ["sydney"] * int(remaining * 0.75)
        + ["atlanta"] * int(remaining * 0.125)
        + ["paris"] * (remaining - int(remaining * 0.75) - int(remaining * 0.125))
    )
    for index, venue in enumerate(venue_plan, start=1):
        name, email, _slug = _person(rng, index)
        affiliation, domain = rng.choice(INSTITUTIONS)
        topics = _weighted_choice(rng, TOPIC_WEIGHTS, rng.choice([2, 3, 3, 4]))
        primary = topics[0]
        intents = _weighted_choice(rng, INTENT_WEIGHTS, rng.choice([1, 1, 2]))
        breakers = rng.sample([b.key for b in taxonomy.DEAL_BREAKERS], k=rng.choice([0, 1, 1, 2]))
        creds: list[tuple[str, bool]] = []
        if rng.random() < 0.35:
            creds.append(("orcid", True))
        if rng.random() < 0.5:
            creds.append(("github", True))
        if rng.random() < 0.3:
            creds.append(("institutional_email", True))
        if rng.random() < 0.12:
            creds.append(("openreview", True))
        labels = [taxonomy.TOPIC_BY_KEY[t].label for t in topics if t in taxonomy.TOPIC_BY_KEY]
        await add_user(
            email=email,
            display_name=name,
            venue=venue,
            affiliation=affiliation,
            domain=domain,
            headline=f"{labels[0] if labels else '机器学习'} · {affiliation}",
            topics=topics,
            papers=[(f"{labels[0] if labels else 'Representation'} 的一次可复现失败复盘", primary)],
            intents=intents,
            bottleneck=rng.choice(BOTTLENECKS.get(primary, ["手上的实验复现率不稳，换卡换库就漂"])),
            contrarian=rng.choice(CONTRARIAN.get(primary, ["这个方向的瓶颈被普遍归错因了"])),
            deal_breakers=breakers,
            avoid_domains=[domain] if rng.random() < 0.06 else [],
            credentials=creds,
            gatekeeper=rng.random() < 0.05,
        )

    stats = await profile_service.rebuild_all(session, rounds=3)
    published = (
        await session.execute(select(func.count(GeneralProfile.user_id)).where(GeneralProfile.is_active.is_(True)))
    ).scalar() or 0
    log("seed_complete", users=len(created) + 1, published=int(published), rounds=stats.get("rounds"))
    return {
        "skipped": False,
        "users": len(created) + 1,
        "published_profiles": int(published),
        "rebuild": stats,
        "password": DEMO_PASSWORD,
    }
