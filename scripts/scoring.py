# -*- coding: utf-8 -*-
"""评分层：将管理人原始指标映射为收益进攻、收益质量、风格偏离三维评分与综合分。

评分口径：
- 每个维度先对产品级指标做 0-100 线性比例打分（min-max 映射：最大值 100 分，最小值 0 分，中间按数值比例线性插值）。
- **按策略线分组打分**：300指增/500指增/1000指增/选股/对冲 各自成池，同类指标在同一策略线池子内独立 min-max（避免跨策略线、跨量纲混排）。
- 管理人在每个维度下的得分，为其旗下产品在该维度得分的均值。
- 风格偏离评分 = 0.7 * 基准相关性分 + 0.3 * (100 - 小市值相关性分)。
- 产品覆盖奖励：5 个产品齐全 ×1.10，4 个 ×1.06，3 个 ×1.03，2 个 ×1.00。
- 综合分 = (攻击分 + 质量分 + 风格分) / 3 * 奖励系数，四舍五入到 0.1。
"""
from __future__ import division
from collections import defaultdict

DIM_NAMES = {
    "attack": "收益进攻评分",
    "quality": "收益质量评分",
    "style": "风格偏离评分",
}


def _percentile_rank(values, higher_is_better=True):
    """对产品指标做 0-100 线性比例打分：最大值 100 分、最小值 0 分，中间按数值比例线性插值（min-max 映射），非按名次等距。"""
    valid = [(idx, float(v)) for idx, v in enumerate(values) if v is not None]
    if not valid:
        return [None] * len(values)
    vals = [v for _, v in valid]
    vmin, vmax = min(vals), max(vals)
    out = [None] * len(values)
    if vmax == vmin:
        for idx, _ in valid:
            out[idx] = 100.0
        return out
    for idx, v in valid:
        if higher_is_better:
            out[idx] = 100.0 * (v - vmin) / (vmax - vmin)
        else:
            out[idx] = 100.0 * (vmax - v) / (vmax - vmin)
    return out


def _safe_mean(values):
    """过滤 None 后求均值，空序列返回 None。"""
    xs = [v for v in values if v is not None]
    return sum(xs) / len(xs) if xs else None


def _reward_factor(n_products):
    """产品覆盖奖励系数。"""
    if n_products >= 5:
        return 1.10
    if n_products == 4:
        return 1.06
    if n_products == 3:
        return 1.03
    return 1.00


def score_all(metrics):
    """主入口：管理人原始指标 -> 三维评分与综合分。

    打分规则：按策略线分组，每个策略线池子独立做 min-max 线性比例打分，
    再按管理人聚合旗下产品得分均值。

    参数:
        metrics: metrics.compute_all 的输出。
    返回:
        包含管理人评分明细与统计结果的字典。
    """
    managers = metrics.get("managers", [])
    if not managers:
        raise ValueError("无满足条件（至少 2 个产品）的管理人，无法评分")

    def build_pools(getter):
        """按策略线构建全局池子: strategy -> (values, owners[(mi, pi)])。"""
        pools = defaultdict(list)
        owners = defaultdict(list)
        for mi, m in enumerate(managers):
            for strat, vals in (getter(m) or {}).items():
                for pi, v in enumerate(vals):
                    pools[strat].append(v)
                    owners[strat].append((mi, pi))
        return pools, owners

    attack_pool, attack_own = build_pools(lambda m: m.get("attack_by_strategy"))
    quality_pool, quality_own = build_pools(lambda m: m.get("quality_by_strategy"))
    bench_pool, bench_own = build_pools(lambda m: m.get("bench_by_strategy"))
    small_pool, small_own = build_pools(lambda m: m.get("small_by_strategy"))

    def rank_pools(pools, owners, higher_is_better=True):
        """每个策略线独立做 min-max 线性比例打分，返回 owner -> score 映射。"""
        out = {}
        for strat, vals in pools.items():
            ranks = _percentile_rank(vals, higher_is_better)
            for owner, r in zip(owners[strat], ranks):
                out[owner] = r
        return out

    attack_ranks = rank_pools(attack_pool, attack_own, True)
    quality_ranks = rank_pools(quality_pool, quality_own, True)
    bench_ranks = rank_pools(bench_pool, bench_own, True)
    small_ranks = rank_pools(small_pool, small_own, True)

    # 将得分回填到每个管理人
    manager_scores = []
    for mi, m in enumerate(managers):
        def collect(ranks, getter):
            scores = []
            for strat, vals in (getter(m) or {}).items():
                for pi in range(len(vals)):
                    r = ranks.get((mi, pi))
                    if r is not None:
                        scores.append(r)
            return scores

        m_attack = collect(attack_ranks, lambda mm: mm.get("attack_by_strategy"))
        m_quality = collect(quality_ranks, lambda mm: mm.get("quality_by_strategy"))
        m_bench = collect(bench_ranks, lambda mm: mm.get("bench_by_strategy"))
        m_small = collect(small_ranks, lambda mm: mm.get("small_by_strategy"))

        attack_score = _safe_mean(m_attack)
        quality_score = _safe_mean(m_quality)
        bench_score = _safe_mean(m_bench)
        small_score = _safe_mean(m_small)

        # 风格偏离评分：基准相关性高分 + 小市值相关性低分
        if bench_score is not None and small_score is not None:
            style_score = 0.7 * bench_score + 0.3 * (100.0 - small_score)
        elif bench_score is not None:
            style_score = bench_score
        elif small_score is not None:
            style_score = 100.0 - small_score
        else:
            style_score = None

        factor = _reward_factor(m["n_products"])
        base = _safe_mean([attack_score, quality_score, style_score])
        total = round(base * factor, 1) if base is not None else None

        manager_scores.append({
            "manager": m["manager"],
            "n_products": m["n_products"],
            "reward_factor": factor,
            "attack_score": round(attack_score, 1) if attack_score is not None else None,
            "quality_score": round(quality_score, 1) if quality_score is not None else None,
            "style_score": round(style_score, 1) if style_score is not None else None,
            "total_score": total,
            "products": m.get("products", []),
            "product_scores": {
                "attack": m_attack,
                "quality": m_quality,
                "bench_corr": m_bench,
                "smallcap_corr": m_small,
            },
            "raw": {
                "attack_by_strategy": m.get("attack_by_strategy", {}),
                "quality_by_strategy": m.get("quality_by_strategy", {}),
                "bench_by_strategy": m.get("bench_by_strategy", {}),
                "small_by_strategy": m.get("small_by_strategy", {}),
            },
        })

    # 按综合分降序
    manager_scores.sort(key=lambda x: x["total_score"] if x["total_score"] is not None else -1, reverse=True)

    return {
        "meta": metrics.get("meta", {}),
        "n_managers": len(manager_scores),
        "dimensions": {
            "attack": {"name": DIM_NAMES["attack"], "weight": 1.0 / 3.0},
            "quality": {"name": DIM_NAMES["quality"], "weight": 1.0 / 3.0},
            "style": {"name": DIM_NAMES["style"], "weight": 1.0 / 3.0},
        },
        "managers": manager_scores,
    }


if __name__ == "__main__":
    import json
    from data_client import fetch_data
    from metrics import compute_all
    data = fetch_data()
    res = score_all(compute_all(data))
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
