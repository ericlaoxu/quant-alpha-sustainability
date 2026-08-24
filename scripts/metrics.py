# -*- coding: utf-8 -*-
"""指标计算层：计算管理人维度的收益进攻、收益质量、风格偏离原始指标。

输入: data_client.fetch_data 输出的内部 schema dict
输出: {"meta":..., "funds":[...], "managers":[...]}
"""
import math

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
    """对产品指标做分位数排名，返回 0-100 的分数。

    最高分获得者 100 分，最低分获得者 0 分，中间按分位数线性插值。
    """
    valid = [(idx, float(v)) for idx, v in enumerate(values) if v is not None]
    if not valid:
        return [None] * len(values)
    sorted_vals = sorted(valid, key=lambda x: x[1], reverse=higher_is_better)
    n = len(sorted_vals)
    out = [None] * len(values)
    for rank, (idx, _) in enumerate(sorted_vals):
        # rank 0 -> 100, rank n-1 -> 0
        out[idx] = 100.0 * (n - 1 - rank) / (n - 1) if n > 1 else 100.0
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

    # 获取评估区间对应的指数收益序列
    index_rets = {}
    for name, closes in indices.items():
        if closes:
            full_rets = _pct_change(closes)
            index_rets[name] = _slice_window(full_rets, dates, eval_start, eval_end)

    # 为每个产品计算评估区间收益序列与指标
    fund_metrics = []
    for f in funds:
        nav = f.get("nav") or []
        excess = f.get("excess_weekly")
        if not nav:
            continue
        full_ret = _pct_change(nav)
        eval_ret = _slice_window(full_ret, dates, eval_start, eval_end)
        eval_excess = None
        if excess:
            eval_excess = _slice_window(excess, dates, eval_start, eval_end)

        strategy = f.get("strategy", "")
        benchmark = STRATEGY_BENCHMARK.get(strategy)
        benchmark_ret = index_rets.get(benchmark) if benchmark else None

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

        # 与对应基准相关性（风格偏离正项）
        if benchmark_ret and len(eval_ret) == len(benchmark_ret):
            record["bench_corr"] = _corr(eval_ret, benchmark_ret)
        else:
            record["bench_corr"] = None

        # 与万得小市值相关性（风格偏离扣分项）
        smallcap_ret = index_rets.get("万得小市值")
        if smallcap_ret and len(eval_ret) == len(smallcap_ret):
            record["smallcap_corr"] = _corr(eval_ret, smallcap_ret)
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

        # 收益进攻评分：300/500/1000/选股产品的近1年年化收益
        attack_values = [fm["annual_return"] for fm in items
                         if fm["strategy"] in ("300指增", "500指增", "1000指增", "选股")]

        # 收益质量评分：300/500/1000的超额收益 + 对冲的夏普比率
        quality_values = []
        for fm in items:
            if fm["strategy"] in ("300指增", "500指增", "1000指增") and fm["annual_excess"] is not None:
                quality_values.append(fm["annual_excess"])
            elif fm["strategy"] == "对冲":
                quality_values.append(fm["sharpe"])

        # 风格偏离评分：300/500/1000的基准相关性与小市值相关性
        bench_corr_values = [fm["bench_corr"] for fm in items
                             if fm["strategy"] in ("300指增", "500指增", "1000指增")
                             and fm["bench_corr"] is not None]
        smallcap_corr_values = [fm["smallcap_corr"] for fm in items
                                if fm["strategy"] in ("300指增", "500指增", "1000指增")
                                and fm["smallcap_corr"] is not None]

        managers.append({
            "manager": manager,
            "n_products": len(items),
            "products": items,
            "attack_values": attack_values,
            "quality_values": quality_values,
            "bench_corr_values": bench_corr_values,
            "smallcap_corr_values": smallcap_corr_values,
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
