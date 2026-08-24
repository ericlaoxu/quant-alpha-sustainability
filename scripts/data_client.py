# -*- coding: utf-8 -*-
"""数据获取层：从 goodluckdata.com 拉取多策略线净值与指数数据。

统一按 2 年数据窗口对齐，用于评估最近 1 年表现。若用户指定 date，则以该日为窗口终点；否则以 API 最新可用日期为终点。

内部 schema（dict）:
{
  "meta": {"source","requested_date","window_start","window_end","eval_start","eval_end","n_weeks","n_eval_weeks","generated_at"},
  "dates": ["YYYY-MM-DD", ...],                # 2 年窗口周度日期（升序）
  "eval_dates": ["YYYY-MM-DD", ...],           # 最近 1 年评估窗口日期
  "indices": {"沪深300":[...], "中证500":[...], "中证1000":[...], "中证2000":[...], "万得小市值":[...]},
  "funds": [
     {"show_name","manager","strategy","scale",
      "nav":[...], "excess_weekly":[...]}
  ]
}
"""
import bisect
import json
import os
from datetime import date, datetime, timedelta


STRATEGY_BENCHMARK = {
    "300指增": "沪深300",
    "500指增": "中证500",
    "1000指增": "中证1000",
}
# 参与评分的全部策略线
VALID_STRATEGIES = ["300指增", "500指增", "1000指增", "选股", "对冲"]
# 需要拉取的宽基指数
INDEX_DASHBOARD_NAMES = ["沪深300", "中证500", "中证1000", "中证2000", "万得小市值"]

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "xu753951"

WINDOW_YEARS = 2
EVAL_YEARS = 1


class DataFetchError(Exception):
    pass


def _today_str():
    """返回当前日期字符串，格式 YYYY-MM-DD。"""
    return date.today().strftime("%Y-%m-%d")


def _str_to_date(s):
    """字符串 YYYY-MM-DD 转 date 对象。"""
    return datetime.strptime(s, "%Y-%m-%d").date()


def _friday_before_or_on(d):
    """返回不晚于 d 的最近周五。"""
    offset = (d.weekday() - 4) % 7
    return d - timedelta(days=offset)


def _generate_fridays(end_date, weeks):
    """生成截至 end_date 的每周五日期序列（升序，共 weeks 个）。"""
    last_fri = _friday_before_or_on(end_date)
    return [(last_fri - timedelta(weeks=weeks - 1 - i)).strftime("%Y-%m-%d") for i in range(weeks)]


def load_config(config_path=None):
    """加载连接配置。

    优先级：config_path 指定的文件 > 环境变量 ALPHA_BASE_URL/ALPHA_USERNAME/ALPHA_PASSWORD > 硬编码默认值。
    默认用户名/密码为 admin/xu753951，base_url 为 https://goodluckdata.com。
    """
    cfg = {
        "base_url": os.environ.get("ALPHA_BASE_URL", "https://goodluckdata.com"),
        "username": os.environ.get("ALPHA_USERNAME", DEFAULT_USERNAME),
        "password": os.environ.get("ALPHA_PASSWORD", DEFAULT_PASSWORD),
        "timeout": 30,
    }
    candidates = [config_path, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                cfg.update({k: v for k, v in loaded.items() if k in cfg})
            except Exception:
                pass
            break
    return cfg


def _http_login(session, cfg):
    """使用 cfg 中的 username/password 登录 goodluckdata.com。"""
    if not cfg.get("username") or not cfg.get("password"):
        raise DataFetchError("HTTP 模式需要配置 username/password")
    url = cfg["base_url"].rstrip("/") + "/api/login/"
    resp = session.post(url, json={"username": cfg["username"], "password": cfg["password"]},
                        timeout=cfg.get("timeout", 30))
    if resp.status_code != 200:
        raise DataFetchError("登录失败：HTTP %s %s" % (resp.status_code, resp.text[:200]))
    body = resp.json()
    if body.get("status") != "success":
        raise DataFetchError("登录失败：%s" % body.get("message", "unknown"))


def _fetch_leaderboard(session, config, strategy, time_range, start_date, end_date):
    """从 /manager/get_leaderboard_data/ 拉取单一策略线的产品净值与累计收益数据。

    返回:
        (dates, stocks)，dates 为日期列表，stocks 为 {show_name: item} 字典。
    """
    params = {
        "time_range": time_range,
        "strategy_1": "量化",
        "strategy_2": strategy,
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    url = config["base_url"].rstrip("/") + "/manager/get_leaderboard_data/"
    resp = session.get(url, params=params, timeout=config.get("timeout", 30))
    if resp.status_code != 200:
        raise DataFetchError("拉取排行榜失败：HTTP %s %s" % (resp.status_code, resp.text[:300]))
    body = resp.json()
    dates = body.get("dates", [])
    stocks = body.get("stocks", {})
    if not dates or not stocks:
        raise DataFetchError("排行榜无数据（dates=%s, stocks=%s）" % (len(dates), len(stocks)))
    return dates, stocks


def _parse_manager(show_name):
    """从产品展示名中解析管理人，展示名格式通常为 管理人·策略线。"""
    parts = show_name.split("·")
    return parts[0] if len(parts) > 1 else show_name


def _build_funds_for_strategy(stocks, dates, strategy):
    """将单一策略线的 stocks 数据转换为内部 fund 列表。"""
    funds = []
    for show_name, item in stocks.items():
        absolute = item.get("absolute") or []
        excess = item.get("excess") or []
        if len(absolute) != len(dates):
            continue
        nav = [1.0 + (float(x) if x is not None else 0.0) for x in absolute]
        excess_weekly = None
        if len(excess) == len(dates):
            excess_weekly = _cum_to_weekly(excess)
        funds.append({
            "show_name": show_name,
            "manager": _parse_manager(show_name),
            "strategy": item.get("strategy") or strategy,
            "scale": None,
            "nav": nav,
            "excess_weekly": excess_weekly,
        })
    return funds


def _fetch_index_series(session, config, fund_dates):
    """从 /api/index_dashboard_data/ 拉取宽基指数周线，并按 fund_dates 对齐。

    返回:
        indices 字典，键为指数名；接口不可用时返回空字典。
    """
    indices = {}
    url = config["base_url"].rstrip("/") + "/api/index_dashboard_data/"
    try:
        resp = session.get(url, timeout=config.get("timeout", 30))
        if resp.status_code != 200:
            raise DataFetchError("HTTP %s" % resp.status_code)
        body = resp.json()
        chart = (body.get("data") or {}).get("chart_data") or {}
        for name in INDEX_DASHBOARD_NAMES:
            rows = chart.get(name) or []
            closes = {row["date"]: float(row["close"]) for row in rows if row.get("close")}
            if not closes:
                continue
            sorted_dates = sorted(closes)
            first_close = closes[sorted_dates[0]]
            series = []
            for d in fund_dates:
                pos = bisect.bisect_right(sorted_dates, d) - 1
                series.append(closes[sorted_dates[pos]] if pos >= 0 else first_close)
            indices[name] = series
    except Exception:
        pass
    return indices


def _cum_to_weekly(cum_series):
    """累计收益序列转换为周度收益序列（首周返回 0）。"""
    out = [0.0]
    prev = 1.0 + (float(cum_series[0]) if cum_series[0] is not None else 0.0)
    for x in cum_series[1:]:
        cur = 1.0 + (float(x) if x is not None else 0.0)
        out.append(cur / prev - 1.0 if prev else 0.0)
        prev = cur
    return out


def _resolve_window(user_date=None):
    """解析 2 年数据窗口与 1 年评估窗口。

    若 user_date 为空，则先用 500指增 请求 API 获取最新日期。
    返回:
        (window_end, window_start, eval_end, eval_start)，均为 YYYY-MM-DD 字符串。
    """
    if user_date:
        window_end = _friday_before_or_on(_str_to_date(user_date)).strftime("%Y-%m-%d")
    else:
        # 先用 2 年窗口请求 500指增，取 API 返回的最新日期作为窗口终点
        config = load_config()
        try:
            import requests
        except ImportError:
            raise DataFetchError("HTTP 模式需要安装 requests：pip install requests")
        today = date.today()
        probe_start = (today - timedelta(days=WINDOW_YEARS * 365 + 30)).strftime("%Y-%m-%d")
        probe_end = today.strftime("%Y-%m-%d")
        session = requests.Session()
        _http_login(session, config)
        dates, _ = _fetch_leaderboard(session, config, "500指增", "近2年", probe_start, probe_end)
        session.close()
        if not dates:
            raise DataFetchError("API 未返回日期数据")
        window_end = dates[-1]
    end_dt = _str_to_date(window_end)
    window_start = (_friday_before_or_on(end_dt - timedelta(days=WINDOW_YEARS * 365)))
    eval_end = window_end
    eval_start = (_friday_before_or_on(end_dt - timedelta(days=EVAL_YEARS * 365)))
    return window_end, window_start.strftime("%Y-%m-%d"), eval_end, eval_start.strftime("%Y-%m-%d")


def fetch_data(managers=None, user_date=None, config=None):
    """统一入口：拉取多策略线净值与指数数据。

    参数:
        managers: 可选，管理人名称列表或逗号分隔字符串；为空则拉取全部管理人。
        user_date: 可选，评估截止日 YYYY-MM-DD；为空则使用 API 最新可用日期。
        config: 可选，配置字典。
    返回:
        内部 schema dict。
    """
    config = config or load_config()
    try:
        import requests
    except ImportError:
        raise DataFetchError("HTTP 模式需要安装 requests：pip install requests")

    window_end, window_start, eval_end, eval_start = _resolve_window(user_date)
    total_weeks = 104
    eval_weeks = 52
    dates = _generate_fridays(_str_to_date(window_end), total_weeks)
    eval_dates = _generate_fridays(_str_to_date(eval_end), eval_weeks)

    session = requests.Session()
    _http_login(session, config)

    all_funds = []
    for strategy in VALID_STRATEGIES:
        try:
            _, stocks = _fetch_leaderboard(session, config, strategy, "近2年", window_start, window_end)
            funds = _build_funds_for_strategy(stocks, dates, strategy)
            all_funds.extend(funds)
        except DataFetchError:
            continue

    indices = _fetch_index_series(session, config, dates)
    session.close()

    # 管理人筛选
    if managers:
        if isinstance(managers, str):
            managers = [m.strip() for m in managers.split(",") if m.strip()]
        manager_set = set(managers)
        all_funds = [f for f in all_funds if f["manager"] in manager_set]

    return {
        "meta": {
            "source": "http",
            "requested_date": user_date or "API最新",
            "window_start": window_start,
            "window_end": window_end,
            "eval_start": eval_start,
            "eval_end": eval_end,
            "n_weeks": total_weeks,
            "n_eval_weeks": eval_weeks,
            "generated_at": _today_str(),
        },
        "dates": dates,
        "eval_dates": eval_dates,
        "indices": indices,
        "funds": all_funds,
    }


if __name__ == "__main__":
    data = fetch_data()
    meta = {k: v for k, v in data.items() if k != "funds"}
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print("funds:", len(data["funds"]))
