"""Controlled vocabularies.

Everything the matching engine compares lives in this file: research clusters with
a two-level generalization ladder, hidden-intent codes with a complementarity
matrix, and a canonical deal-breaker vocabulary.

Why a fixed vocabulary at all? Because free-text features cannot be k-anonymized.
A tag drawn from a 48-cluster vocabulary has a measurable cohort size; a phrase
copied out of someone's paper does not. The taxonomy is the price of admission for
publishing anything at all.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# venues
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Venue:
    key: str
    label: str
    city: str
    tz: str
    dates: str
    is_primary: bool = False


VENUES: dict[str, Venue] = {
    "sydney": Venue("sydney", "Sydney · ICC", "Sydney", "Australia/Sydney", "Dec 8–10", True),
    "atlanta": Venue("atlanta", "Atlanta · Satellite", "Atlanta", "America/New_York", "Dec 9–13"),
    "paris": Venue("paris", "Paris · Satellite", "Paris", "Europe/Paris", "Dec 9–13"),
}

VENUE_SESSIONS: dict[str, list[dict[str, str]]] = {
    "sydney": [
        {"ref": "hall-a-keynote", "label": "Hall A · Keynote", "zone": "Level 1"},
        {"ref": "poster-2a", "label": "Poster Board 2A · RL & Decision", "zone": "Level 2"},
        {"ref": "poster-2b", "label": "Poster Board 2B · Systems & Efficiency", "zone": "Level 2"},
        {"ref": "poster-3c", "label": "Poster Board 3C · Generative Models", "zone": "Level 3"},
        {"ref": "coffee-level2", "label": "Coffee Corner · Level 2", "zone": "Level 2"},
        {"ref": "workshop-r1", "label": "Workshop Room R1 · Efficient Inference", "zone": "Level 4"},
    ],
    "atlanta": [
        {"ref": "gwcc-hall", "label": "GWCC Hall · Main Track", "zone": "Building B"},
        {"ref": "poster-atl", "label": "Poster Hall · Atlanta", "zone": "Building B"},
    ],
    "paris": [
        {"ref": "palais-hall", "label": "Palais · Main Track", "zone": "Level 0"},
        {"ref": "poster-par", "label": "Poster Hall · Paris", "zone": "Level 1"},
    ],
}


# ---------------------------------------------------------------------------
# research taxonomy: umbrella (L2) <- cluster (L1) <- raw strings
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    umbrella: str
    aliases: tuple[str, ...]


UMBRELLAS: dict[str, str] = {
    "learning": "学习与优化",
    "generative": "生成模型",
    "language": "语言与推理",
    "systems": "系统与效率",
    "perception": "感知与多模态",
    "science": "AI for Science",
    "safety": "安全与对齐",
    "theory": "理论与统计",
    "applied": "应用与产品",
    "data": "数据与评测",
}

TOPICS: tuple[Topic, ...] = (
    # ---- RL / learning -------------------------------------------------
    Topic("rl_policy_opt", "RL 策略优化", "learning", ("rlhf", "ppo", "grpo", "策略梯", "policy optimization", "reinforcement learning", "强化学习", "rlvr")),
    Topic("rl_offline", "离线与偏好 RL", "learning", ("dpo", "偏好优化", "offline rl", "偏好对齐", "preference learning")),
    Topic("rl_env_agent", "Agent 环境与交互学习", "learning", ("agent rl", "environment", "tool use rl", "交互学习", "多智能体训练")),
    Topic("optim_theory", "优化理论与收敛", "theory", ("sgd", "convergence", "优化器", "convex", "scaling law 理论")),
    Topic("repr_learning", "表示学习", "learning", ("self-supervised", "对比学习", "contrastive", "embedding", "表征")),
    Topic("continual", "持续与增量学习", "learning", ("continual", "catastrophic forgetting", "增量学习", "finite memory")),
    Topic("meta_general", "泛化与归纳偏置", "learning", ("generalization bound", "o.o.d", "out-of-distribution", "分布外", "泛化")),
    Topic("meta_learn", "元学习与少样本", "learning", ("meta-learning", "few-shot", "少样本", "in-context")),
    # ---- generative ----------------------------------------------------
    Topic("diffusion", "扩散模型", "generative", ("diffusion", "扩散", "flow matching", "整流流", "score-based")),
    Topic("video_gen", "视频与时空生成", "generative", ("video generation", "视频生成", "temporal consistency", "world model 生成")),
    Topic("3d_gen", "3D 与场景生成", "generative", ("3d generation", "nerf", "gaussian splatting", "场景生成", "4d")),
    Topic("autoregressive_gen", "自回归与统一生成", "generative", ("autoregressive image", "token-based generation", "统一生成", "any-to-any")),
    Topic("gen_eval", "生成质量与评测", "data", ("fid", "human preference", "生成评测", "aesthetic")),
    Topic("gen_theory", "生成模型理论", "theory", ("score matching theory", "sampling theory", "生成理论", "langevin")),
    # ---- language / reasoning ------------------------------------------
    Topic("long_context", "长上下文与记忆", "language", ("long context", "长上下文", "context window", "memory", "kv cache 压缩", "检索记忆")),
    Topic("reasoning_test_time", "测试时推理与搜索", "language", ("chain-of-thought", "test-time", "搜索与推理", "cot", "推理时计算", "deliberation")),
    Topic("rlvr", "可验证奖励推理", "language", ("rlvr", "verifiable reward", "可验证奖励", "math reasoning", "代码推理")),
    Topic("rag", "检索增强", "language", ("rag", "retrieval", "检索增强", "vector search", "知识注入")),
    Topic("multilingual", "多语言与低资源", "language", ("multilingual", "多语言", "low-resource", "低资源")),
    Topic("speech", "语音与音频", "perception", ("speech", "asr", "语音", "tts", "音频生成", "voice clone")),
    # ---- systems / efficiency -----------------------------------------
    Topic("inference_serving", "推理服务与调度", "systems", ("serving", "vllm", "sglang", "推理引擎", "batching", "paged attention", "推理服务")),
    Topic("kv_cache", "KV Cache 与显存管理", "systems", ("kv cache", "paged", "prefix caching", "显存碎片", "cache eviction", "长尾延迟")),
    Topic("distributed_train", "分布式训练与通信", "systems", ("fsdp", "deepspeed", "张量并行", "all-reduce", "通信优化", "千卡")),
    Topic("rl_rollout_infra", "RL rollout 基础设施", "systems", ("rollout", "异步 rl", "采样服务", "off-policy 流水线", "训练推理协同")),
    Topic("quantization", "量化与压缩", "systems", ("quantization", "int4", "量化", "蒸馏", "pruning", "动态剪枝")),
    Topic("kernel_compiler", "算子与编译器", "systems", ("cuda kernel", "triton", "算子", "编译", "tvm")),
    Topic("edge_ondevice", "端侧与移动推理", "systems", ("on-device", "端侧", "mobile", "边缘推理", "低功耗")),
    Topic("hpc_scaling", "大规模集群与能效", "systems", ("cluster", "能效", "功耗", "scheduling 集群", "interconnect")),
    # ---- perception / multimodal ---------------------------------------
    Topic("vlm", "视觉语言模型", "perception", ("vlm", "视觉语言", "multimodal reasoning", "grounding")),
    Topic("detection", "检测与分割", "perception", ("detection", "segmentation", "检测", "分割")),
    Topic("embodied", "具身与机器人", "perception", ("embodied", "robot", "具身", "manipulation", "vla")),
    Topic("medical_imaging", "医学影像", "science", ("medical imaging", "医学影像", "病理", "radiolog")),
    Topic("eeg_bci", "神经信号与 BMI", "science", ("eeg", "bci", "脑机", "fMRI", "神经解码")),
    # ---- science -------------------------------------------------------
    Topic("protein", "蛋白与分子设计", "science", ("protein", "蛋白", "molecule", "分子生成", "docking")),
    Topic("materials", "材料与化学", "science", ("materials", "材料", "chemistry", "catalyst", "晶体")),
    Topic("physics_sim", "物理仿真与 PDE", "science", ("pde", "physics-informed", "流体", "仿真", "neural operator")),
    Topic("weather_climate", "气象与气候", "science", ("weather", "气象", "climate", "气候预测")),
    Topic("bio_genomics", "基因组与生信", "science", ("genomics", "基因", "single-cell", "单细胞", "bioinformatics")),
    # ---- safety / alignment --------------------------------------------
    Topic("alignment", "对齐与价值观", "safety", ("alignment", "对齐", "constitutional", "价值对齐", "harmlessness")),
    Topic("interpretability", "可解释性", "safety", ("interpretability", "mechanistic", "可解释", "circuits", "probe")),
    Topic("jailbreak", "越狱与对抗攻击", "safety", ("jailbreak", "adversarial", "越狱", "prompt injection", "red team")),
    Topic("privacy_ml", "隐私与联邦学习", "safety", ("differential privacy", "federated", "隐私计算", "tee", "k-anonymity")),
    Topic("fairness", "公平性与偏差", "safety", ("fairness", "bias", "公平", "偏差")),
    Topic("eval_benchmark", "评测与基准", "data", ("benchmark", "评测", "eval suite", "leaderboard", "contamination")),
    Topic("agent_memory", "Agent 记忆与工具编排", "applied", ("agent framework", "mcp", "tool orchestration", "agent memory", "智能体编排")),
    Topic("agent_eval", "Agent 评测与可靠性", "data", ("agent benchmark", "agent 可靠性", "agent eval", "长程任务")),
    # ---- applied / product ---------------------------------------------
    Topic("recsys", "推荐与排序", "applied", ("recommendation", "推荐", "ranking", "ctr", "召回")),
    Topic("fin_ai", "金融与量化 AI", "applied", ("quant", "金融", "trading", "风控")),
    Topic("health_app", "医疗与健康应用", "applied", ("clinical", "健康", "医疗应用", "ehr")),
    Topic("edu_ai", "教育与交互", "applied", ("education", "教育", "tutoring", "教学")),
    Topic("graphics", "图形与渲染", "applied", ("rendering", "渲染", "graphics", "shader")),
    Topic("data_engine", "数据工程与合成数据", "data", ("synthetic data", "合成数据", "data curation", "清洗", "标注")),
)


def topic_index() -> dict[str, Topic]:
    return {t.key: t for t in TOPICS}


TOPIC_BY_KEY = topic_index()


def classify_topics(raw: list[str], limit: int = 8) -> list[str]:
    """Map free-text topic strings onto the controlled vocabulary.

    Deterministic substring matching, most-specific-first. Anything that matches
    nothing is dropped rather than invented, because an unmapped topic cannot be
    k-anonymized and would leak through the generalization layer.
    """
    found: list[str] = []
    hay = " || ".join(x.lower() for x in raw if x)
    if not hay:
        return found
    for topic in TOPICS:
        if topic.key in found:
            continue
        for alias in topic.aliases:
            if alias in hay:
                found.append(topic.key)
                break
        if len(found) >= limit:
            break
    return found


def umbrella_of(topic_key: str) -> str:
    topic = TOPIC_BY_KEY.get(topic_key)
    return topic.umbrella if topic else "applied"


def umbrella_label(key: str) -> str:
    return UMBRELLAS.get(key, key)


# ---------------------------------------------------------------------------
# hidden intent
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Intent:
    key: str
    label: str
    bucket: str  # cofounder | hiring | job | funding | research | social
    risk: str  # why people hide this; informs the card language


INTENTS: tuple[Intent, ...] = (
    Intent("cofounder_infra", "寻找系统/Infra 方向联创", "cofounder", "employment_risk"),
    Intent("cofounder_algo", "寻找算法/研究向联创", "cofounder", "employment_risk"),
    Intent("cofounder_product", "寻找产品/商业化联创", "cofounder", "employment_risk"),
    Intent("cofounder_biz", "寻找 GTM/商务联创", "cofounder", "employment_risk"),
    Intent("job_quiet", "低调看新机会", "job", "employment_risk"),
    Intent("job_intern", "找实习/博后位置", "job", "status_risk"),
    Intent("hiring_research", "招研究员", "hiring", "none"),
    Intent("hiring_infra", "招工程/Infra", "hiring", "none"),
    Intent("hiring_intern", "招实习/访问", "hiring", "none"),
    Intent("funding_raising", "融资/接触投资人", "funding", "status_risk"),
    Intent("investing", "看项目/投早期", "funding", "none"),
    Intent("research_collab", "找研究合作", "research", "scooping_risk"),
    Intent("research_feedback", "想找人当面掰扯技术争议", "research", "scooping_risk"),
    Intent("peer_friendship", "就想交点同行朋友", "social", "none"),
)

INTENT_BY_KEY = {i.key: i for i in INTENTS}

# complementary[intent] = intents whose presence makes a pair worth a sandbox slot
COMPLEMENTS: dict[str, tuple[str, ...]] = {
    "cofounder_infra": ("cofounder_algo", "cofounder_product", "cofounder_biz", "job_quiet", "investing"),
    "cofounder_algo": ("cofounder_infra", "cofounder_product", "cofounder_biz", "investing"),
    "cofounder_product": ("cofounder_algo", "cofounder_infra", "cofounder_biz", "investing"),
    "cofounder_biz": ("cofounder_algo", "cofounder_infra", "cofounder_product", "investing"),
    "job_quiet": ("hiring_research", "hiring_infra", "cofounder_infra", "cofounder_algo", "investing"),
    "job_intern": ("hiring_intern", "hiring_research", "research_collab"),
    "hiring_research": ("job_quiet", "job_intern", "research_collab", "cofounder_algo"),
    "hiring_infra": ("job_quiet", "job_intern", "cofounder_infra"),
    "hiring_intern": ("job_intern", "research_collab"),
    "funding_raising": ("investing", "cofounder_biz"),
    "investing": ("funding_raising", "cofounder_algo", "cofounder_infra", "cofounder_biz", "job_quiet"),
    "research_collab": ("research_collab", "research_feedback", "hiring_research", "job_intern", "cofounder_algo"),
    "research_feedback": ("research_collab", "research_feedback"),
    "peer_friendship": ("peer_friendship",),
}

# Pairs banned outright even if the matrix calls them complementary. Deliberately
# empty for now: the one pairing that looks dangerous (quiet job seeker x
# recruiter) is *wanted* matchmaking, and it is made safe by the mutual
# affiliation-domain blocklist rather than by an intent rule. Keeping the hook
# here documents that this decision was made on purpose.
INTENT_PAIR_BANS: tuple[tuple[str, str], ...] = ()


def complementary(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return b in COMPLEMENTS.get(a, ()) or a in COMPLEMENTS.get(b, ())


def need_bucket_labels() -> dict[str, str]:
    return {
        "cofounder": "找联创",
        "hiring": "招人",
        "job": "看机会",
        "funding": "融资 / 投资",
        "research": "研究合作",
        "social": "同行社交",
    }


# ---------------------------------------------------------------------------
# deal breakers (canonical vocabulary; free text is mapped onto it)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DealBreaker:
    key: str
    label: str
    keywords: tuple[str, ...]


DEAL_BREAKERS: tuple[DealBreaker, ...] = (
    DealBreaker("ppt_only", "只讲商业模式、没碰过底层", ("ppt", "商业模式", "画饼", "只讲愿景", "business plan")),
    DealBreaker("no_oss_record", "没有开源/工程交付记录", ("没有开源", "no opensource", "无开源", "没写过代码", "纯论文")),
    DealBreaker("no_scale", "没做过大规模训练或真实线上系统", ("没有千卡", "小规模 toy", "toy setting", "只跑过小模型")),
    DealBreaker("pure_theory", "纯理论、不关心落地", ("纯理论", "只关心理论", "purely theoretical")),
    DealBreaker("hype_only", "追热点、没有独立判断", ("追热点", "ai washing", "跟风", "蹭概念")),
    DealBreaker("recruiter_spam", "群发招聘/推销", ("群发", "mass outreach", "推销", "sales pitch")),
    DealBreaker("credential_showoff", "只堆头衔不谈具体问题", ("只谈头衔", "堆 title", "credentials only")),
    DealBreaker("no_commitment", "聊完不落地、放鸽子", ("放鸽子", "no-show", "承诺不落地")),
)

DEAL_BREAKER_BY_KEY = {d.key: d for d in DEAL_BREAKERS}


def map_deal_breakers(free_text: list[str]) -> list[str]:
    """Map attendee wording onto canonical keys; unmapped text is discarded (privacy)."""
    keys: list[str] = []
    hay = " || ".join(x.lower() for x in free_text if x)
    if not hay:
        return keys
    for breaker in DEAL_BREAKERS:
        if any(k in hay for k in breaker.keywords):
            keys.append(breaker.key)
    return keys


# ---------------------------------------------------------------------------
# deal-breaker detection used by the scoring engine on peer utterances
# ---------------------------------------------------------------------------
HYPE_MARKERS: tuple[str, ...] = (
    "颠覆",
    "revolutionary",
    "world-class",
    "行业第一",
    "guaranteed",
    "十倍提升",
    "即将上市",
    "万亿市场",
)

SUBSTANCE_MARKERS: tuple[str, ...] = (
    "因为我",
    "实测",
    "我们试过",
    "吞吐",
    "延迟",
    "profile",
    "benchmark",
    "复现",
    "踩坑",
    "trade-off",
    "权衡",
)


# ---------------------------------------------------------------------------
# meeting slots (deterministic, no calendar integration required for the demo)
# ---------------------------------------------------------------------------
SLOTS: tuple[dict[str, str], ...] = (
    {"id": "d1-1430", "label": "Day 1 · 14:30–14:50（两场 Oral 之间）", "zone": "Level 2 Coffee Corner"},
    {"id": "d1-1700", "label": "Day 1 · 17:00–17:20（Poster 收摊前）", "zone": "Level 2 Coffee Corner"},
    {"id": "d2-1040", "label": "Day 2 · 10:40–11:00（Coffee break）", "zone": "Level 1 Atrium"},
    {"id": "d2-1615", "label": "Day 2 · 16:15–16:35（Poster 间隙）", "zone": "Level 2 Poster 2B"},
    {"id": "d3-1200", "label": "Day 3 · 12:00–13:00（Lunch 拼桌）", "zone": "Level 3 Dining"},
)

GROUP_SLOTS: tuple[dict[str, str], ...] = (
    {"id": "g-d2-lunch", "label": "Day 2 · 12:30 拼桌午餐", "zone": "Level 3 Dining"},
    {"id": "g-d2-evening", "label": "Day 2 · 19:00 主题晚餐", "zone": "Darling Harbour"},
    {"id": "g-d3-coffee", "label": "Day 3 · 15:30 咖啡圆桌", "zone": "Level 2 Coffee Corner"},
)
