"""Deterministic sandbox engine.

No model, no network, no spend — and still a real state machine, real redaction,
real gate. This is what CI runs against, what the offline demo runs against, and
the fallback when a gateway is down mid-conference.

The dialogue is templated on purpose. A hand-written template that says something
concrete about paged attention beats a hallucinating model that says something
plausible about paged attention the person never did.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.domain import taxonomy
from app.domain.scoring import deterministic_score, PeerView, SideContext
from app.llm.base import PeerLike, SideLike


def _pick(options: list[str], *seed: str) -> str:
    digest = hashlib.blake2b("|".join(seed).encode(), digest_size=4).digest()
    return options[int.from_bytes(digest, "big") % len(options)]


# topic key -> (probe question when facing this topic, mechanism I can offer, trade-off I pay)
TOPIC_DIALOGUE: dict[str, tuple[str, str, str]] = {
    "kv_cache": (
        "长尾延迟到底是卡在通信带宽，还是卡在显存碎片导致的搬移",
        "分页调度加上把换入换出挪到流水线的空档里，先削峰再谈压缩",
        "页表回收的延迟会让峰值显存短暂翻倍，需要算法侧容忍 1–2 个 step 的偏差",
    ),
    "inference_serving": (
        "你们的 batching 是静态分组还是随到随组，冷启动那段怎么压",
        "把请求按长度分桶，再做前缀共享，短请求不被长请求拖尾",
        "分桶会牺牲一点吞吐上限，换来的是 P99 不被最慢的那条拖死",
    ),
    "rl_rollout_infra": (
        "训练和采样之间是全同步还是细粒度异步，偏了之后怎么校正",
        "采样侧做权重双缓冲，页表延迟回收，避免热更新时的显存峰值",
        "代价是 off-policy 偏差变大，需要在算法侧加重要性比修正",
    ),
    "distributed_train": (
        "千卡规模下你是先扛通信还是先扛显存",
        "按拓扑重排 all-reduce，把跨机流量压到最低那一层",
        "重排后单卡利用率会有波动，需要动态批大小兜住",
    ),
    "quantization": (
        "量化是训练后直接压，还是带校准的逐层搜索",
        "逐层敏感度排序后再决定位宽，别一刀切 int4",
        "敏感层保住精度，但整体延迟收益会打折扣",
    ),
    "long_context": (
        "长上下文是靠稀疏注意力还是外挂检索",
        "分层记忆加动态剪枝，只让真正相关的片段进注意力",
        "剪枝策略错了会丢关键证据，需要留一条回退路径",
    ),
    "reasoning_test_time": (
        "推理时算力是砸在搜索宽度还是验证深度上",
        "先做窄搜索再用可验证的打分器回收，避免宽度爆炸",
        "验证器本身有偏，会把模型带向它擅长而非正确的那条路",
    ),
    "rlvr": (
        "可验证奖励是靠单测通过率还是靠形式化的约束",
        "用单测加人工抽检的双轨，避免模型学到奖赏漏洞",
        "双轨没法全自动，样本吞吐会掉一个量级",
    ),
    "diffusion": (
        "采样步数是靠蒸馏压还是靠更好的求解器",
        "求解器换成二阶后步数能砍一半，再叠加轻量蒸馏",
        "蒸馏带来多样性下降，需要按场景开关",
    ),
    "interpretability": (
        "你们验证机制解释是靠消融还是靠跨模型一致性",
        "用跨规模一致性做交叉验证，单模型消融太容易自证",
        "跨规模实验成本高，只能在小规模上做全扫",
    ),
    "jailbreak": (
        "你们是把攻击当红队样例还是当分布外检测",
        "把攻击样本当训练信号，同时保留一层独立判别器",
        "判别器会误杀边缘正常请求，需要分层阈值",
    ),
    "eval_benchmark": (
        "评测污染怎么排，是靠时间切分还是靠人工重写",
        "时间切分加人工重写双管，单靠切分挡不住隐式泄漏",
        "重写成本极高，覆盖不了全量",
    ),
    "vlm": (
        "视觉侧的特征对齐是在预训练做还是后期接",
        "早对齐省事但容易被语言侧压制，我倾向分层对齐",
        "分层对齐会增加工程复杂度，训练不稳定",
    ),
}

UMBRELLA_QUESTION = {
    "learning": "你说的策略更新是同步的还是有延迟的",
    "generative": "你的采样质量和多样性之间怎么权衡",
    "language": "你的推理路径是搜索出来的还是训出来的",
    "systems": "瓶颈在显存、带宽还是调度",
    "perception": "你的标注噪声是怎么处理的",
    "science": "你的数据规模离真实可用还差多少",
    "safety": "你的评估集能不能代表真实攻击面",
    "theory": "你的假设在真实数据分布上成立吗",
    "applied": "线上流量和离线指标差多少",
    "data": "你的数据清洗策略是可复现的吗",
}


def _probe_question(peer: PeerLike) -> str:
    for tag in peer.tags:
        if tag in TOPIC_DIALOGUE:
            return TOPIC_DIALOGUE[tag][0]
    for tag in peer.tags:
        if tag in taxonomy.UMBRELLAS:
            return UMBRELLA_QUESTION.get(tag, "你在这个方向最硬的那次失败是什么")
    return "你在最近的实验里最反直觉的一个观测是什么"


def _mechanism(side: SideLike) -> tuple[str, str]:
    for tag in side.tags:
        if tag in TOPIC_DIALOGUE:
            return TOPIC_DIALOGUE[tag][1], TOPIC_DIALOGUE[tag][2]
    if side.contrarian:
        return side.contrarian, "这条判断可能被更强的基线推翻，我留着对照组"
    return (
        "先把指标拆到能定位到具体组件，再针对那个组件做局部替换",
        "局部替换会牺牲整体最优，需要接受一段时间的负收益",
    )


class DeterministicEngine:
    name = "deterministic"
    is_live = False

    async def respond(
        self,
        *,
        speaker: SideLike,
        peer: PeerLike,
        round_no: int,
        peer_utterances: list[str],
        max_tokens: int = 420,
        session_ref: str = "",
    ) -> str:
        mechanism, tradeoff = _mechanism(speaker)
        pain = (speaker.bottleneck or mechanism)[:70]
        if round_no == 1:
            lead = _pick(
                [
                    f"我这边最实在的卡点是{pain}。",
                    f"我现在的具体问题是{pain}。",
                    f"卡住我的是同一个具体环节：{pain}。",
                ],
                speaker.user_id,
                "r1",
            )
            return f"{lead}{_probe_question(peer)}？如果这个点你踩过，我想知道你当时怎么取舍。"
        peer_text = " ".join(peer_utterances)[:160]
        probe = _probe_question(peer)
        lead = _pick(
            [
                f"关于{probe}，我的做法是{mechanism}。",
                f"直接回答：{mechanism}。",
                f"我这边是{mechanism}。",
            ],
            speaker.user_id,
            "r2",
        )
        return (
            f"{lead}代价是{tradeoff}。如果你那边是纯概念层面的答案，我就先不占用线下时间了。"
            if peer_text
            else f"{lead}我看见的代价是{tradeoff}。"
        )

    async def judge(
        self,
        *,
        rater: SideLike,
        peer: PeerLike,
        transcript: list[str],
        max_tokens: int = 620,
        session_ref: str = "",
    ) -> dict[str, Any]:
        rater_ctx = SideContext(
            user_id=rater.user_id,
            tags=rater.tags,
            intents=rater.intents,
            buckets=rater.buckets,
            bottleneck=rater.bottleneck,
            contrarian=rater.contrarian,
            deal_breakers=rater.deal_breakers,
            blocklist_domains=rater.blocklist_domains,
            forbidden_terms=rater.forbidden_terms,
        )
        peer_view = PeerView(
            alias=peer.alias,
            tags=peer.tags,
            buckets=peer.buckets,
            utterances=peer.utterances or transcript,
        )
        result = deterministic_score(rater_ctx, peer_view)
        return {
            "fit_score": result.fit_score,
            "hit_deal_breaker": result.hit_deal_breaker,
            "evidence": result.evidence,
            "icebreaker": result.icebreaker,
            "injected": False,
        }

