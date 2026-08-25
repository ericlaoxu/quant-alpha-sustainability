# -*- coding: utf-8 -*-
"""指标计算层：计算管理人维度的收益进攻、收益质量、风格偏离原始指标。

输入: data_client.fetch_data 输出的内部 schema dict
输出: {"meta":..., "funds":[...], "managers":[...]}
"""
import math
from collections import defaultdict

import numpy as np

MIN_MANAGER_PRODUCTS = 2  # 管理人至少需有 2 个产品才参与评分
ROLL_WINDOW = 26          # 滚动窗口（周）

STRATEGY_BENCHMARK = {
    "300指增": "沪深300",
    "500指增": "中证500",
    "1000指增": "中证1000",
}


def _pct_change(series):
    """将价格序列转换为收益率序列，首项为 0。"""
    out = [0.0]
    prev = float(series[0]) if series else 1.0
    for x in series[1:]:
        cur = float(x)
        out.append(cur / prev - 1.0 if prev else 0.0)
        prev = cur
    return out


def _mean(xs):
    """计算均值，空序列返回 0。"""
    xs = [float(x) for x in xs if x is not None]
    return float(np.mean(xs)) if xs else 0.0


def _std(xs):
    """计算样本标准差，长度不足 2 返回 0。"""
    xs = [float(x) for x in xs if x is not None]
    return float(np.std(xs, ddof=1)) if len(xs) > 1 else 0.0


def _corr(a, b):
    """计算两组序列的皮尔逊相关系数，样本不足或方差为 0 返回 0。"""
    a = np.asarray([float(x) for x in a if x is not None], dtype=float)
    b = np.asarray([float(x) for x in b if x is not None], dtype=float)
    if len(a) < 4 or np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _annualize(ret_series):
    """周度收益序列 -> 年化收益（复利 52 周）。"""
    xs = [float(x) for x in ret_series if x is not None]
    if not xs:
        return 0.0
    total = 1.0
    for x in xs:
        total *= (1.0 + x)
    n = len(xs)
    return total ** (52.0 / n) - 1.0 if n > 0 else 0.0


def _sharpe(ret_series):
    """周度收益序列 -> 年化夏普比率（假设无风险利率为 0）。"""
    xs = [float(x) for x in ret_series if x is not None]
    if not xs:
        return 0.0
    mean = _mean(xs) * 52.0
    std = _std(xs) * math.sqrt(52.0)
    return mean / std if std > 0 else 0.0


def _slice_window(series, dates, start_date, end_date):
    """按日期区间截取序列，返回区间内子序列。"""
    start_idx = next((i for i, d in enumerate(dates) if d >= start_date), 0)
    end_idx = next((i for i, d in enumerate(dates) if d > end_date), len(dates))
    return series[start_idx:end_idx]


def _percentile_rank(values, higher_is_better=True):
    """对产品指标做 0-100 线性比例打分：最大值 100 分、最小值 0 分，中间按数值比例线性插值（min-max 映射）。"""
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


def compute_all(data):
    """计算全部管理人维度的原始指标。

    参数:
        data: data_client.fetch_data 输出的内部 schema dict。
    返回:
        包含 meta / funds / managers 的字典。
    """
    meta = data["meta"]
    dates = data["dates"]
    eval_dates = data["eval_dates"]
    eval_start = meta["eval_start"]
    eval_end = meta["eval_end"]
    indices = data.get("indices", {})
    funds = data.get("funds", [])

    # 获取评估区间对应的指数收盘点位序列（累计收益口径：净值 vs 归一化指数点位）
    index_closes = {}
    for name, closes in indices.items():
        if closes:
            index_closes[name] = _slice_window(closes, dates, eval_start, eval_end)

    # 为每个产品计算评估区间收益序列与指标
    fund_metrics = []
    for f in funds:
        nav = f.get("nav") or []
        excess = f.get("excess_weekly")
        if not nav:
            continue
        full_ret = _pct_change(nav)
        eval_ret = _slice_window(full_ret, dates, eval_start, eval_end)
        eval_nav = _slice_window(nav, dates, eval_start, eval_end)
        eval_excess = None
        if excess:
            eval_excess = _slice_window(excess, dates, eval_start, eval_end)

        strategy = f.get("strategy", "")
        benchmark = STRATEGY_BENCHMARK.get(strategy)
        bench_closes = index_closes.get(benchmark) if benchmark else None

        record = {
            "show_name": f["show_name"],
            "manager": f["manager"],
            "strategy": strategy,
            "scale": f.get("scale"),
            "annual_return": _annualize(eval_ret),
            "sharpe": _sharpe(eval_ret),
        }
        if eval_excess:
            record["annual_excess"] = _annualize(eval_excess)
            record["excess_sharpe"] = _sharpe(eval_excess)
        else:
            record["annual_excess"] = None
            record["excess_sharpe"] = None

        # 与对应基准相关性：优先使用 correlation 接口值（近1年累计口径，含周一/周五相位修正），否则本地计算（净值 vs 归一化指数点位）
        if f.get("bench_corr") is not None:
            record["bench_corr"] = f["bench_corr"]
        elif bench_closes and len(eval_nav) == len(bench_closes) and bench_closes[0]:
            bench_norm = [x / bench_closes[0] for x in bench_closes]
            record["bench_corr"] = _corr(eval_nav, bench_norm)
        else:
            record["bench_corr"] = None

        # 与万得小市值相关性：优先使用 correlation 接口值，否则本地计算（累计收益口径）
        smallcap_closes = index_closes.get("万得小市值")
        if f.get("smallcap_corr") is not None:
            record["smallcap_corr"] = f["smallcap_corr"]
        elif smallcap_closes and len(eval_nav) == len(smallcap_closes) and smallcap_closes[0]:
            small_norm = [x / smallcap_closes[0] for x in smallcap_closes]
            record["smallcap_corr"] = _corr(eval_nav, small_norm)
        else:
            record["smallcap_corr"] = None

        fund_metrics.append(record)

    # 按管理人聚合
    manager_map = {}
    for fm in fund_metrics:
        manager_map.setdefault(fm["manager"], []).append(fm)

    managers = []
    for manager, items in manager_map.items():
        if len(items) < MIN_MANAGER_PRODUCTS:
            continue

        # 收益进攻：300/500/1000/选股的年化收益，按策略线分组（各策略线独立 min-max）
        attack_by_strategy = defaultdict(list)
        for fm in items:
            if fm["strategy"] in ("300指增", "500指增", "1000指增", "选股"):
                attack_by_strategy[fm["strategy"]].append(fm["annual_return"])

        # 收益质量：指增的信息比率（超额夏普）、对冲的夏普，按策略线分组（同类指标各自 min-max，避免跨量纲混排）
        quality_by_strategy = defaultdict(list)
        for fm in items:
            if fm["strategy"] in ("300指增", "500指增", "1000指增") and fm["excess_sharpe"] is not None:
                quality_by_strategy[fm["strategy"]].append(fm["excess_sharpe"])
            elif fm["strategy"] == "对冲" and fm["sharpe"] is not None:
                quality_by_strategy["对冲"].append(fm["sharpe"])

        # 风格偏离：基准相关性、小市值相关性，按策略线分组
        bench_by_strategy = defaultdict(list)
        small_by_strategy = defaultdict(list)
        for fm in items:
            if fm["strategy"] in ("300指增", "500指增", "1000指增"):
                if fm["bench_corr"] is not None:
                    bench_by_strategy[fm["strategy"]].append(fm["bench_corr"])
                if fm["smallcap_corr"] is not None:
                    small_by_strategy[fm["strategy"]].append(fm["smallcap_corr"])

        managers.append({
            "manager": manager,
            "n_products": len(items),
            "products": items,
            "attack_by_strategy": dict(attack_by_strategy),
            "quality_by_strategy": dict(quality_by_strategy),
            "bench_by_strategy": dict(bench_by_strategy),
            "small_by_strategy": dict(small_by_strategy),
        })

    return {
        "meta": meta,
        "funds": fund_metrics,
        "managers": managers,
    }


if __name__ == "__main__":
    import json
    from data_client import fetch_data
    data = fetch_data()
    res = compute_all(data)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
