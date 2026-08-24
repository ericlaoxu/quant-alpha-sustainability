# -*- coding: utf-8 -*-
"""评分层：将管理人原始指标映射为收益进攻、收益质量、风格偏离三维评分与综合分。

评分口径：
- 每个维度先对产品级指标做分位数排名（第一名 100 分，最后一名 0 分）。
- 管理人在每个维度下的得分，为其旗下产品在该维度得分的均值。
- 风格偏离评分 = 0.7 * 基准相关性分 + 0.3 * (100 - 小市值相关性分)。
- 产品覆盖奖励：5 个产品齐全 ×1.10，4 个 ×1.06，3 个 ×1.03，2 个 ×1.00。
- 综合分 = (攻击分 + 质量分 + 风格分) / 3 * 奖励系数，四舍五入到 0.1。
"""
from __future__ import division

DIM_NAMES = {
    "attack": "收益进攻评分",
    "quality": "收益质量评分",
    "style": "风格偏离评分",
}


def _percentile_rank(values, higher_is_better=True):
    """对产品指标做分位数排名，返回 0-100 的分数。"""
    valid = [(idx, float(v)) for idx, v in enumerate(values) if v is not None]
    if not valid:
        return [None] * len(values)
    sorted_vals = sorted(valid, key=lambda x: x[1], reverse=higher_is_better)
    n = len(sorted_vals)
    out = [None] * len(values)
    for rank, (idx, _) in enumerate(sorted_vals):
        out[idx] = 100.0 * (n - 1 - rank) / (n - 1) if n > 1 else 100.0
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

    参数:
        metrics: metrics.compute_all 的输出。
    返回:
        包含管理人评分明细与统计结果的字典。
    """
    managers = metrics.get("managers", [])
    if not managers:
        raise ValueError("无满足条件（至少 2 个产品）的管理人，无法评分")

    # 收集所有产品指标用于全局分位数排名
    attack_values = []
    quality_values = []
    bench_corr_values = []
    smallcap_corr_values = []

    attack_owners = []    # (manager_index, product_index_in_manager)
    quality_owners = []
    bench_corr_owners = []
    smallcap_corr_owners = []

    for mi, m in enumerate(managers):
        for pi, v in enumerate(m.get("attack_values", [])):
            attack_values.append(v)
            attack_owners.append((mi, pi))
        for pi, v in enumerate(m.get("quality_values", [])):
            quality_values.append(v)
            quality_owners.append((mi, pi))
        for pi, v in enumerate(m.get("bench_corr_values", [])):
            bench_corr_values.append(v)
            bench_corr_owners.append((mi, pi))
        for pi, v in enumerate(m.get("smallcap_corr_values", [])):
            smallcap_corr_values.append(v)
            smallcap_corr_owners.append((mi, pi))

    # 全局分位数排名
    attack_ranks = _percentile_rank(attack_values, higher_is_better=True)
    quality_ranks = _percentile_rank(quality_values, higher_is_better=True)
    bench_corr_ranks = _percentile_rank(bench_corr_values, higher_is_better=True)
    smallcap_ranks = _percentile_rank(smallcap_corr_values, higher_is_better=True)

    # 将排名回填到每个管理人
    manager_scores = []
    for mi, m in enumerate(managers):
        m_attack = []
        m_quality = []
        m_bench = []
        m_small = []

        for rank, (rmi, rpi) in zip(attack_ranks, attack_owners):
            if rmi == mi:
                m_attack.append(rank)
        for rank, (rmi, rpi) in zip(quality_ranks, quality_owners):
            if rmi == mi:
                m_quality.append(rank)
        for rank, (rmi, rpi) in zip(bench_corr_ranks, bench_corr_owners):
            if rmi == mi:
                m_bench.append(rank)
        for rank, (rmi, rpi) in zip(smallcap_ranks, smallcap_corr_owners):
            if rmi == mi:
                m_small.append(rank)

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
                "attack_values": m.get("attack_values", []),
                "quality_values": m.get("quality_values", []),
                "bench_corr_values": m.get("bench_corr_values", []),
                "smallcap_corr_values": m.get("smallcap_corr_values", []),
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
