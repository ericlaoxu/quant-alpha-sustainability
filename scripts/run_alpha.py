# -*- coding: utf-8 -*-
"""量化管理人综合能力评估 — CLI 编排入口。

用法示例：
  # 全市场管理人评估（不传任何参数）
  python run_alpha.py --report --out report.md

  # 指定多个管理人
  python run_alpha.py --managers "幻方,九坤,明汯" --report --out report.md

  # 指定评估截止日（自动往前取 2 年数据，评估最近 1 年）
  python run_alpha.py --date 2026-07-17 --managers "幻方,九坤" --report --out report.md

数据流：fetch_data -> metrics.compute_all -> scoring.score_all
输出：stdout JSON；--report 输出 Markdown 报告（含风险提示）。
"""
from __future__ import print_function
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_client import fetch_data, load_config, DataFetchError, STRATEGY_BENCHMARK  # noqa: E402
import metrics as metrics_mod  # noqa: E402
import scoring as scoring_mod  # noqa: E402

_CN_NUMS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
             "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
             "二十一", "二十二", "二十三", "二十四", "二十五", "二十六", "二十七", "二十八", "二十九", "三十"]


def _pct(x, nd=1):
    """将数值格式化为百分比字符串，None 返回 '-'。"""
    return "-" if x is None else ("%.*f%%" % (nd, float(x) * 100.0))


def _num(x, nd=2):
    """将数值格式化为固定位数小数，None 返回 '-'。"""
    return "-" if x is None else ("%.*f" % (nd, float(x)))


# ---------------------------------------------------------------- 证据行

def _manager_evidence(m):
    """生成单个管理人的证据文本列表。"""
    lines = []
    lines.append("- 产品数量：**%d**（奖励系数：**%.2f**）" % (m["n_products"], m["reward_factor"]))
    if m.get("attack_score") is not None:
        lines.append("- 收益进攻评分：**%.1f**" % m["attack_score"])
    if m.get("quality_score") is not None:
        lines.append("- 收益质量评分：**%.1f**" % m["quality_score"])
    if m.get("style_score") is not None:
        lines.append("- 风格偏离评分：**%.1f**" % m["style_score"])
    lines.append("- 综合评分：**%.1f**" % m["total_score"])
    return lines


def _product_details(m):
    """生成管理人旗下产品原始指标明细。"""
    lines = []
    lines.append("| 产品 | 策略 | 年化收益 | 年化超额/夏普 | 基准相关性 | 小市值相关性 |")
    lines.append("|---|---|---|---|---|---|")
    products = m.get("products", [])
    for p in products:
        strategy = p.get("strategy", "-")
        qual = p.get("annual_excess") if p.get("annual_excess") is not None else p.get("sharpe")
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            p.get("show_name", "-"), strategy, _pct(p.get("annual_return")),
            _num(qual, 2), _num(p.get("bench_corr"), 3), _num(p.get("smallcap_corr"), 3)))
    return lines


def _manager_attribution(m):
    """生成单个管理人的归因解读，必须引用具体数值。"""
    lines = []
    products = m.get("products", [])
    name = m.get("manager", "-")

    # 进攻：年化收益最高（且参与进攻评分）的产品
    attack_ps = [p for p in products
                 if p.get("annual_return") is not None
                 and p.get("strategy") in ("300指增", "500指增", "1000指增", "选股")]
    if attack_ps:
        top = max(attack_ps, key=lambda p: p.get("annual_return", 0))
        lines.append("- **收益进攻（%.1f 分）**：%s·%s 近1年年化收益 %s，为旗下最高，"
                     "带动进攻分处于同类前列。" % (
                         m.get("attack_score") or 0,
                         name, top.get("strategy", "-"),
                         _pct(top.get("annual_return"))))

    # 质量：年化超额最高（指增）或夏普最高（对冲）的产品
    quality_ps = []
    for p in products:
        if p.get("strategy") in ("300指增", "500指增", "1000指增") and p.get("annual_excess") is not None:
            quality_ps.append((p, "年化超额 %s" % _pct(p.get("annual_excess"))))
        elif p.get("strategy") == "对冲" and p.get("sharpe") is not None:
            quality_ps.append((p, "夏普比率 %.2f" % p.get("sharpe")))
    if quality_ps:
        top, metric_desc = max(quality_ps, key=lambda x: x[0].get("annual_excess") if x[0].get("annual_excess") is not None else x[0].get("sharpe", 0))
        lines.append("- **收益质量（%.1f 分）**：%s·%s 的 %s 表现最强，"
                     "支撑质量分排名。" % (
                         m.get("quality_score") or 0,
                         name, top.get("strategy", "-"),
                         metric_desc))

    # 风格：基准相关性与小市值相关性
    bench_ps = [p for p in products
                if p.get("bench_corr") is not None
                and p.get("strategy") in ("300指增", "500指增", "1000指增")]
    small_ps = [p for p in products
                if p.get("smallcap_corr") is not None
                and p.get("strategy") in ("300指增", "500指增", "1000指增")]
    if bench_ps or small_ps:
        bench_part = ""
        if bench_ps:
            top_bench = max(bench_ps, key=lambda p: p.get("bench_corr", 0))
            bench_part = "%s·%s 与基准（%s）相关系数 %.3f" % (
                name, top_bench.get("strategy", "-"),
                STRATEGY_BENCHMARK.get(top_bench.get("strategy", "")),
                top_bench.get("bench_corr"))
        small_part = ""
        if small_ps:
            top_small = max(small_ps, key=lambda p: p.get("smallcap_corr", 0))
            small_part = "%s·%s 与万得小市值相关系数 %.3f" % (
                name, top_small.get("strategy", "-"),
                top_small.get("smallcap_corr"))
        style_desc = "。".join([x for x in [bench_part, small_part] if x])
        lines.append("- **风格偏离（%.1f 分）**：%s。" % (m.get("style_score") or 0, style_desc))

    lines.append("- **综合分（%.1f 分）**：由三维得分均值乘以产品覆盖奖励系数 %.2f 得到，"
                 "反映该管理人在本评估区间内的相对综合能力。" % (
                     m.get("total_score") or 0, m.get("reward_factor")))
    return lines


# ---------------------------------------------------------------- 报告渲染

def render_report(result):
    """将评分结果渲染为 Markdown 报告。

    参数:
        result: scoring.score_all 返回的字典。
    返回:
        Markdown 字符串。
    """
    meta = result.get("meta", {})
    dims = result.get("dimensions", {})
    managers = result.get("managers", [])
    lines = []
    lines.append("# 量化管理人综合能力评估报告")
    lines.append("")
    lines.append("## 一、评估要素")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("|---|---|")
    lines.append("| 评估区间 | %s 至 %s（%d 周） |" % (meta.get("eval_start", "-"), meta.get("eval_end", "-"), meta.get("n_eval_weeks", 0)))
    lines.append("| 数据窗口 | %s 至 %s（%d 周） |" % (meta.get("window_start", "-"), meta.get("window_end", "-"), meta.get("n_weeks", 0)))
    lines.append("| 数据来源 | goodluckdata.com HTTP 接口 |")
    lines.append("| 参与管理人 | %d |" % result.get("n_managers", 0))
    lines.append("| 生成时间 | %s |" % meta.get("generated_at", "-"))
    if meta.get("requested_date") and meta["requested_date"] != "API最新":
        lines.append("| 用户指定截止日 | %s |" % meta["requested_date"])
    not_found = result.get("not_found_managers")
    if not_found:
        lines.append("| 未匹配管理人 | %s（请核对名称） |" % "、".join(not_found))
    lines.append("")
    lines.append("## 二、评分规则")
    lines.append("")
    lines.append("1. **收益进攻评分**：300/500/1000指增及选股产品的近1年年化收益，全市场分位数排名（第一 100 分，最后 0 分）。")
    lines.append("2. **收益质量评分**：300/500/1000指增的近1年年化超额收益、对冲产品的近1年夏普比率，全市场分位数排名。")
    lines.append("3. **风格偏离评分**：300/500/1000指增的近1年收益序列与对应指数相关性（正项，权重 70%），以及与万得小市值指数相关性（扣分项，权重 30%）。")
    lines.append("4. **产品覆盖奖励**：同一管理人产品数量越多，奖励系数越高；5 个产品 ×1.10，4 个 ×1.06，3 个 ×1.03，2 个 ×1.00。")
    lines.append("5. **综合分** = （进攻 + 质量 + 风格）/ 3 × 奖励系数。")
    lines.append("")
    lines.append("## 三、管理人排名")
    lines.append("")
    not_found = result.get("not_found_managers")
    if not_found:
        lines.append("> 以下输入的管理人名称未匹配到数据，请核对：%s。" % "、".join(not_found))
        lines.append("")
    lines.append("| 排名 | 管理人 | 产品数 | 进攻 | 质量 | 风格 | 奖励系数 | 综合 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for idx, m in enumerate(managers, 1):
        lines.append("| %d | %s | %d | %.1f | %.1f | %.1f | %.2f | %.1f |" % (
            idx, m["manager"], m["n_products"],
            m.get("attack_score") or 0, m.get("quality_score") or 0,
            m.get("style_score") or 0, m["reward_factor"], m["total_score"]))
    lines.append("")

    # 重点管理人归因解读（仅展示前 3 名）
    lines.append("## 四、重点管理人归因解读")
    lines.append("")
    lines.append("> 本部分引用管理人排名表及管理人明细中的具体数值，解释各管理人在收益进攻、"
                 "收益质量、风格偏离三个维度上的相对优势与短板。")
    lines.append("")
    for idx, m in enumerate(managers[:3], 1):
        lines.append("### 4.%d %s（综合 %.1f 分）" % (idx, m["manager"], m.get("total_score") or 0))
        lines.append("")
        lines.extend(_manager_attribution(m))
        lines.append("")

    # 各管理人明细（仅展示前 10 名）
    section = 5
    for m in managers[:10]:
        lines.append("## %s、管理人明细：%s" % (_CN_NUMS[section - 1], m["manager"]))
        section += 1
        lines.append("")
        lines.extend(_manager_evidence(m))
        lines.append("")
        lines.append("**归因解读：**")
        lines.append("")
        lines.extend(_manager_attribution(m))
        lines.append("")
        lines.append("**产品原始指标：**")
        lines.append("")
        lines.extend(_product_details(m))
        lines.append("")

    lines.append("## %s、风险提示" % _CN_NUMS[section - 1])
    lines.append("")
    lines.append("> 本报告仅衡量管理人在特定区间内的相对表现，不构成对未来收益的预测，亦不构成任何投资建议。"
                 "历史业绩不代表未来表现，私募产品存在净值波动与本金损失风险；管理人旗下产品不足 2 个时不参与评分。"
                 "数据来源与计算口径可能存在偏差，请以基金管理人正式披露信息为准。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程

def main(argv=None):
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="量化管理人综合能力评估")
    parser.add_argument("--managers", default=None,
                        help="管理人名称，多个用逗号分隔（例：幻方,九坤,明汯）；不传则评估全市场")
    parser.add_argument("--date", default=None,
                        help="评估截止日 YYYY-MM-DD；不传则使用 API 返回的最新日期")
    parser.add_argument("--report", action="store_true", help="输出 Markdown 报告")
    parser.add_argument("--out", default=None, help="输出文件路径（默认打印到 stdout）")
    parser.add_argument("--config", default=None, help="config.json 路径（可选，覆盖环境变量与默认值）")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        managers = [m.strip() for m in args.managers.split(",") if m.strip()] if args.managers else None
        # 始终拉取全市场数据，以保证分位数排名基于全市场；再按用户指定管理人过滤
        data = fetch_data(user_date=args.date, config=config)
        metrics_result = metrics_mod.compute_all(data)
        result = scoring_mod.score_all(metrics_result)

        if managers:
            manager_set = set(managers)
            filtered = [m for m in result["managers"] if m["manager"] in manager_set]
            result["managers"] = filtered
            result["n_managers"] = len(filtered)
            found_names = {m["manager"] for m in filtered}
            result["not_found_managers"] = [name for name in managers if name not in found_names]

        if args.report:
            output = render_report(result)
        else:
            output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(output + ("\n" if args.report else ""))
            print("已写入：%s" % os.path.abspath(args.out))
        else:
            print(output)
    except (DataFetchError, ValueError) as e:
        sys.stderr.write("错误：%s\n" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
