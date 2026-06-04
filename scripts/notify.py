"""
LOF Arbiter - 通知模块

生成结构化套利报告，供 GitHub Issue / 邮件等渠道使用。

环境变量配置（邮件）：
  SMTP_HOST      - SMTP 服务器地址 (默认 smtp.qq.com)
  SMTP_PORT      - SMTP 端口 (默认 465)
  SMTP_USER      - 发件人邮箱
  SMTP_PASS      - SMTP 授权码 (非邮箱密码)
  NOTIFY_TO      - 收件人邮箱 (多个用逗号分隔)
  NOTIFY_SUBJECT - 邮件主题前缀 (可选)
"""

import os
import smtplib
import html as html_module
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional, List, Dict, Any

import pandas as pd

from scripts.jisilu import (
    JISILU_LOF_LIST_URL,
    LYLOF_ARBITRAGE_URL,
    TRADESMART_LOF_URL,
    REPORT_PAGE_URL,
    jisilu_detail_url,
    eastmoney_fund_url,
    ths_fund_url,
    lylof_arbitrage_url,
    tradesmart_lof_url,
    xueqiu_fund_url,
    clean_fund_code,
    format_fund_external_links,
)
from scripts.query import format_onsite_subscribe_limit, format_onsite_subscribe_min

REPORT_TOP_N = 10


def get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def send_report(
    report_text: str,
    to_addrs: Optional[List[str]] = None,
    subject_prefix: str = "",
    smtp_host: str = "",
    smtp_port: int = 0,
    smtp_user: str = "",
    smtp_pass: str = "",
) -> bool:
    """发送套利报告邮件（纯文本）。"""
    host = smtp_host or get_env("SMTP_HOST", "smtp.qq.com")
    port = smtp_port or int(get_env("SMTP_PORT", "465"))
    user = smtp_user or get_env("SMTP_USER")
    password = smtp_pass or get_env("SMTP_PASS")
    to_list = to_addrs or [
        addr.strip()
        for addr in get_env("NOTIFY_TO").split(",")
        if addr.strip()
    ]

    if not user or not password or not to_list:
        print("[notify] SMTP credentials or recipients not configured, skip")
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    subject = (
        f"[{subject_prefix}] LOF 套利日报 - {now}"
        if subject_prefix
        else f"LOF 套利日报 - {now}"
    )

    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject

    body = f"""LOF 套利日报
{'=' * 60}
生成时间：{now}

{report_text}

{'=' * 60}
由 LOF Arbiter Bot 自动发送
"""
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(user, password)
                server.sendmail(user, to_list, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                server.login(user, password)
                server.sendmail(user, to_list, msg.as_string())

        print(f"[notify] Report sent to {len(to_list)} recipient(s)")
        return True
    except Exception as e:
        print(f"[notify] Failed to send: {e}")
        return False


def _confidence_label(method: str, confidence: str) -> str:
    labels = {
        "TRACKING_HK": "港股同步",
        "TRACKING_US": "美股QDII",
        "TRACKING_DOM": "国内LOF",
        "TRACKING": "跟踪估算",
        "LEGACY": "官方净值",
    }
    base = labels.get(method or "", method or "未知")
    if confidence == "ESTIMATE":
        return f"{base}·仅供参考"
    return base


def _format_turnover(turnover) -> str:
    turnover_wan = (turnover or 0) / 10000
    if turnover_wan >= 10000:
        return f"{turnover_wan / 10000:.2f}亿"
    if turnover_wan >= 1:
        return f"{turnover_wan:.2f}万"
    return f"{turnover_wan * 10000:.0f}元"


def _format_onsite_subscribe_limit(daily_limit) -> str:
    return format_onsite_subscribe_limit(daily_limit, empty_label="—")


def _format_onsite_subscribe_min(fund_code) -> str:
    return format_onsite_subscribe_min(fund_code)


def _format_premium_md(premium) -> str:
    if premium is None or pd.isna(premium):
        return "—"
    if premium > 1:
        return f"**+{premium:.2f}%**"
    if premium < -1:
        return f"**{premium:.2f}%**"
    return f"{premium:+.2f}%"


def _status_badge(status: str) -> str:
    status = str(status or "")
    if "限" in status:
        return "限购"
    if "暂停" in status:
        return "暂停"
    if "开放" in status:
        return "开放"
    return status or "未知"


def _load_report_context(
    db_path: str = "",
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from scripts.query import (
        get_lof_data,
        get_premium_top,
        get_discount_top,
        get_limited_premium_top,
        get_suspended_premium_top,
        DEFAULT_DB_PATH,
    )

    db = db_path or DEFAULT_DB_PATH
    now = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    df_all = get_lof_data(db_path=db)

    if df_all.empty:
        return {
            "now": now,
            "db_path": db,
            "empty": True,
            "df_all": df_all,
            "df_limited": df_all,
            "df_premium": df_all,
            "df_suspended": df_all,
            "df_discount": df_all,
        }

    return {
        "now": now,
        "db_path": db,
        "empty": False,
        "df_all": df_all,
        "df_limited": get_limited_premium_top(n=REPORT_TOP_N, min_premium=0.3),
        "df_premium": get_premium_top(n=REPORT_TOP_N, min_premium=0.5),
        "df_suspended": get_suspended_premium_top(n=REPORT_TOP_N, min_premium=0.5),
        "df_discount": get_discount_top(n=REPORT_TOP_N, min_discount=0.5),
    }


def _report_meta_lines(now: str, html: bool = False) -> List[str]:
    ref_links = (
        f"[集思录 LOF 列表]({JISILU_LOF_LIST_URL}) · "
        f"[路远套利]({LYLOF_ARBITRAGE_URL}) · "
        f"[TradeSmart]({TRADESMART_LOF_URL}) · "
        f"[在线日报]({REPORT_PAGE_URL})"
    )
    if html:
        ref_links = (
            f'<a href="{JISILU_LOF_LIST_URL}">集思录 LOF 列表</a> · '
            f'<a href="{LYLOF_ARBITRAGE_URL}">路远套利</a> · '
            f'<a href="{TRADESMART_LOF_URL}">TradeSmart</a> · '
            f'<a href="{REPORT_PAGE_URL}">在线日报</a>'
        )
    link_hint = (
        "集思录 / 东财 / 同花顺 / 路远 / TradeSmart / 雪球"
    )
    if html:
        return [
            f"<p class=\"meta\"><strong>生成时间</strong>：{now}（北京时间）</p>",
            f"<p class=\"meta\"><strong>数据来源</strong>：LOF Arbiter 自动 ETL · {ref_links}</p>",
            f"<p class=\"meta\"><strong>溢价说明</strong>：基于 T-2 锚定 + R(T-1) 估算，非实际净值 · "
            f"单基金外链：{link_hint}</p>",
        ]
    return [
        f"> **生成时间**：{now}（北京时间）  ",
        f"> **数据来源**：LOF Arbiter 自动 ETL · {ref_links}  ",
        f"> **溢价说明**：基于 T-2 锚定 + R(T-1) 估算，非实际净值 · 单基金外链：{link_hint}",
    ]


def _html_escape(text) -> str:
    return html_module.escape(str(text or ""))


def _html_premium_cell(premium) -> str:
    if premium is None or pd.isna(premium):
        return '<td class="num muted premium-focus">—</td>'
    value = float(premium)
    if value > 0:
        css = "num premium-up premium-focus"
    elif value < 0:
        css = "num premium-down premium-focus"
    else:
        css = "num muted premium-focus"
    text = f"{value:+.2f}%"
    if abs(value) > 1:
        text = f"<strong>{text}</strong>"
    return f'<td class="{css}">{text}</td>'


def _html_status_pill(status: str) -> str:
    status = str(status or "")
    if "暂停" in status:
        return '<span class="pill pill-danger">暂停</span>'
    if "限" in status:
        return '<span class="pill pill-warn">限购</span>'
    if "开放" in status:
        return '<span class="pill pill-ok">开放</span>'
    label = _html_escape(status or "未知")
    return f'<span class="pill pill-muted">{label}</span>'


def _html_confidence_pill(method: str, confidence: str) -> str:
    label = _confidence_label(method, confidence)
    if str(method or "").startswith("TRACKING"):
        css = "pill pill-info"
    else:
        css = "pill pill-muted"
    return f'<span class="{css}">{_html_escape(label)}</span>'


def _html_link_pills(fund_code: str) -> str:
    links = [
        ("集思录", jisilu_detail_url(fund_code)),
        ("东财", eastmoney_fund_url(fund_code)),
        ("同花顺", ths_fund_url(fund_code)),
        ("路远", lylof_arbitrage_url()),
        ("TS", tradesmart_lof_url()),
        ("雪球", xueqiu_fund_url(fund_code)),
    ]
    return "".join(
        f'<a class="link-pill" href="{url}" target="_blank" rel="noopener">{label}</a>'
        for label, url in links
    )


_HTML_PAGE_STYLES = """
:root {
  --bg: #f5f1ea;
  --bg-soft: #fdfcfa;
  --bg-hover: #fdf8ef;
  --card: #ffffff;
  --ink: #1a1a2e;
  --muted: #5c6d7e;
  --muted-light: #9ca3af;
  --border: #e5e0d8;
  --border-soft: #f0ebe3;
  --accent: #8b6914;
  --accent-soft: rgba(139, 105, 20, 0.1);
  --code: #2c7dc3;
  --up: #dc2626;
  --down: #16a34a;
  --focus: #fffbf0;
  --header: #1a1a2e;
  --shadow: 0 1px 2px rgba(26, 26, 46, 0.06);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Microsoft YaHei", sans-serif;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
.page {
  max-width: 1180px;
  margin: 0 auto;
  padding: 28px 16px 56px;
}
.hero { margin-bottom: 28px; }
.hero-top {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.hero h1 {
  margin: 0;
  font-size: 1.65rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}
.count-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
}
.subtitle {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 14px;
}
.sources {
  margin: 6px 0 0;
  color: var(--muted-light);
  font-size: 12px;
}
.sources a, .footer a { color: var(--accent); text-decoration: none; }
.sources a:hover, .footer a:hover { text-decoration: underline; }
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
  margin-top: 18px;
}
.stat-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: var(--shadow);
}
.stat-card .label {
  font-size: 12px;
  color: var(--muted-light);
}
.stat-card .value {
  margin-top: 4px;
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.section { margin-bottom: 28px; }
.section-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.section-head h2 {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 700;
}
.section-count {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  background: var(--card);
  border: 1px solid var(--border);
}
.section-hint {
  margin: 0 0 12px;
  color: var(--muted);
  font-size: 13px;
}
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: var(--shadow);
  overflow: hidden;
}
.table-scroll { overflow-x: auto; }
table.data {
  width: 100%;
  min-width: 1060px;
  border-collapse: collapse;
  font-size: 13px;
}
table.data thead tr {
  background: var(--header);
  color: rgba(255, 255, 255, 0.92);
}
table.data th {
  padding: 10px 10px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
table.data th.col-center,
table.data td.num,
table.data td.code,
table.data td.links {
  text-align: center;
}
table.data th.col-focus {
  background: var(--accent);
  color: #fff;
}
table.data td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-soft);
  vertical-align: middle;
}
table.data tbody tr:nth-child(even) { background: var(--bg-soft); }
table.data tbody tr:hover { background: var(--bg-hover); }
table.data tbody tr:last-child td { border-bottom: none; }
td.code {
  text-align: center;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}
td.code a { color: var(--code); text-decoration: none; }
td.code a:hover { text-decoration: underline; }
td.name {
  max-width: 220px;
  font-weight: 600;
}
td.name a { color: var(--ink); text-decoration: none; }
td.name a:hover { color: var(--accent); }
td.num {
  text-align: center;
  font-variant-numeric: tabular-nums;
}
td.muted, .muted { color: var(--muted-light); }
.premium-up { color: var(--up); font-weight: 600; }
.premium-down { color: var(--down); font-weight: 600; }
.premium-focus {
  background: var(--focus);
  font-weight: 700;
}
td.links { white-space: nowrap; }
.link-pill {
  display: inline-block;
  margin: 1px 2px 1px 0;
  padding: 2px 7px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 500;
  color: var(--muted);
  background: #f5f1ea;
  border: 1px solid var(--border);
  text-decoration: none;
}
.link-pill:hover {
  color: var(--accent);
  border-color: var(--accent);
}
.pill {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 6px;
  font-size: 11px;
  line-height: 1.3;
  font-weight: 600;
  white-space: nowrap;
}
.pill-ok {
  color: #059669;
  background: #ecfdf5;
  border: 1px solid #d1fae5;
}
.pill-danger {
  color: #ef4444;
  background: #fef2f2;
  border: 1px solid #fee2e2;
}
.pill-warn {
  color: #d97706;
  background: #fffbeb;
  border: 1px solid #fde68a;
}
.pill-info {
  color: #2563eb;
  background: #eff6ff;
  border: 1px solid #dbeafe;
}
.pill-muted {
  color: var(--muted);
  background: #f5f1ea;
  border: 1px solid var(--border);
}
.risk-card .risk-row {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-soft);
  font-size: 13px;
}
.risk-card .risk-row:last-child { border-bottom: none; }
.risk-card .risk-type { font-weight: 600; color: var(--ink); }
.risk-card .risk-desc { color: var(--muted); }
.footer {
  margin-top: 36px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
  color: var(--muted-light);
  font-size: 12px;
  text-align: center;
}
.empty {
  padding: 28px 16px;
  text-align: center;
  color: var(--muted-light);
  font-size: 14px;
}
@media (max-width: 640px) {
  .hero h1 { font-size: 1.35rem; }
  .stat-card .value { font-size: 18px; }
}
"""


def _fund_table_html(df: pd.DataFrame, show_status: bool = False) -> str:
    if df.empty:
        return '<div class="card"><p class="empty">今日暂无满足条件的品种</p></div>'

    status_th = '<th class="col-center">申购</th>' if show_status else ""
    head = f"""
    <thead><tr>
      <th class="col-center">代码</th>
      <th>名称</th>
      <th class="col-center">现价</th>
      <th class="col-center col-focus">溢价</th>
      <th class="col-center">预估净值</th>
      <th class="col-center">成交额</th>
      <th class="col-center">场内最低</th>
      <th class="col-center">场内最高</th>
      {status_th}
      <th class="col-center">置信度</th>
      <th class="col-center">外链</th>
    </tr></thead>
    """

    body_rows = []
    for _, row in df.iterrows():
        est_nav = row.get("estimated_nav") or row.get("nav") or row.get("prev_nav")
        est_str = f"{est_nav:.4f}" if est_nav is not None and not pd.isna(est_nav) else "—"
        code_raw = row.get("fund_code_full") or row.get("fund_code") or ""
        code_clean = clean_fund_code(code_raw)
        js_url = jisilu_detail_url(code_raw)
        name = _html_escape(row.get("fund_name", ""))
        price = row.get("price")
        price_str = f"¥{price:.4f}" if price else "—"

        status_td = ""
        if show_status:
            status_td = f'<td class="num">{_html_status_pill(row.get("purchase_status", ""))}</td>'

        body_rows.append(
            f"<tr>"
            f'<td class="code"><a href="{js_url}" target="_blank" rel="noopener">{code_clean}</a></td>'
            f'<td class="name"><a href="{js_url}" target="_blank" rel="noopener" title="{name}">{name}</a></td>'
            f'<td class="num">{price_str}</td>'
            f"{_html_premium_cell(row.get('premium_rate'))}"
            f'<td class="num">{est_str}</td>'
            f'<td class="num">{_format_turnover(row.get("turnover"))}</td>'
            f'<td class="num">{_format_onsite_subscribe_min(code_raw)}</td>'
            f'<td class="num">{_format_onsite_subscribe_limit(row.get("daily_limit"))}</td>'
            f"{status_td}"
            f'<td class="num">{_html_confidence_pill(row.get("estimation_method", ""), row.get("premium_confidence", ""))}</td>'
            f'<td class="links">{_html_link_pills(code_raw)}</td>'
            f"</tr>"
        )

    return (
        '<div class="card"><div class="table-scroll">'
        f'<table class="data">{head}<tbody>{"".join(body_rows)}</tbody></table>'
        "</div></div>"
    )


def _estimation_summary_html(df: pd.DataFrame) -> str:
    if df.empty or "estimation_method" not in df.columns:
        return ""

    summary = df["estimation_method"].value_counts()
    method_labels = {
        "TRACKING_HK": "港股同步",
        "TRACKING_US": "美股QDII",
        "TRACKING_DOM": "国内LOF",
        "TRACKING": "通用跟踪",
        "LEGACY": "官方净值",
        "NONE": "无数据",
    }
    tracking_count = sum(
        count for method, count in summary.items() if str(method).startswith("TRACKING")
    )

    cards = [
        ("覆盖基金", len(df)),
        ("可跟踪估算", tracking_count),
    ]
    for method, count in summary.head(4).items():
        cards.append((method_labels.get(method, method), count))

    return '<div class="stat-grid">' + "".join(
        f'<div class="stat-card"><div class="label">{_html_escape(label)}</div>'
        f'<div class="value">{count}</div></div>'
        for label, count in cards
    ) + "</div>"


def _html_section(title: str, hint: str, table_html: str, count: int = 0) -> str:
    count_badge = f'<span class="section-count">{count} 只</span>' if count else ""
    return (
        f'<section class="section">'
        f'<div class="section-head"><h2>{_html_escape(title)}</h2>{count_badge}</div>'
        f'<p class="section-hint">{_html_escape(hint)}</p>'
        f"{table_html}"
        f"</section>"
    )


def _html_risk_section() -> str:
    rows = [
        ("交割周期", "LOF 套利 T+2 交割，资金占用约 2 个交易日"),
        ("赎回费用", "持有 ≥7 天通常 0.5%，不足 7 天约 1.5%"),
        ("流动性", "高溢价不等于能成交，需关注成交额"),
        ("暂停申购", "第五节品种不可申购套利，仅供已有份额的场内卖出参考"),
        ("美股 QDII", "仅「当日溢价」可信，次日预估不可作为交易依据"),
        ("港股 QDII", "当日/次日溢价均可较精准计算"),
    ]
    body = "".join(
        f'<div class="risk-row"><div class="risk-type">{t}</div><div class="risk-desc">{d}</div></div>'
        for t, d in rows
    )
    return (
        _html_section("六、风险提示", "操作前请结合流动性、限额与基金公司公告综合判断", "")
        .replace("</section>", f'<div class="card risk-card">{body}</div></section>', 1)
    )


def _fund_table(df: pd.DataFrame, show_status: bool = False) -> str:
    if df.empty:
        return "_今日暂无满足条件的品种_\n"

    headers = ["基金", "代码", "当日溢价", "现价", "预估净值", "成交额", "场内最低", "场内最高", "外链", "置信度"]
    if show_status:
        headers.insert(8, "申购状态")

    rows = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]

    for _, row in df.iterrows():
        est_nav = row.get("estimated_nav") or row.get("nav") or row.get("prev_nav")
        est_str = f"{est_nav:.4f}" if est_nav is not None and not pd.isna(est_nav) else "—"
        code_raw = row.get("fund_code_full") or row.get("fund_code") or ""
        code_display = str(code_raw)
        js_url = jisilu_detail_url(code_raw)
        name = str(row.get("fund_name", ""))

        cells = [
            f"[{name}]({js_url})",
            f"[{code_display}]({js_url})",
            _format_premium_md(row.get("premium_rate")),
            f"{row.get('price', 0):.3f}" if row.get("price") else "—",
            est_str,
            _format_turnover(row.get("turnover")),
            _format_onsite_subscribe_min(row.get("fund_code_full") or row.get("fund_code")),
            _format_onsite_subscribe_limit(row.get("daily_limit")),
        ]
        if show_status:
            cells.append(_status_badge(row.get("purchase_status", "")))
        cells.append(format_fund_external_links(code_raw))
        cells.append(_confidence_label(row.get("estimation_method", ""), row.get("premium_confidence", "")))
        rows.append("| " + " | ".join(cells) + " |")

    return "\n".join(rows) + "\n"


def _estimation_summary_md(df: pd.DataFrame) -> str:
    if "estimation_method" not in df.columns:
        return "| 指标 | 数值 |\n| --- | --- |\n| 覆盖基金数 | — |\n"

    summary = df["estimation_method"].value_counts()
    method_labels = {
        "TRACKING_HK": "港股同步跟踪",
        "TRACKING_US": "美股QDII跟踪",
        "TRACKING_DOM": "国内LOF跟踪",
        "TRACKING": "通用跟踪",
        "LEGACY": "官方净值降级",
        "NONE": "无数据",
    }

    lines = [
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 覆盖基金数 | {len(df)} |",
    ]
    for method, count in summary.items():
        label = method_labels.get(method, method)
        lines.append(f"| {label} | {count} |")

    tracking_count = sum(
        count for method, count in summary.items() if str(method).startswith("TRACKING")
    )
    lines.append(f"| 可跟踪估算 | {tracking_count} |")
    return "\n".join(lines) + "\n"


def generate_report_markdown(
    db_path: str = "",
    generated_at: Optional[str] = None,
) -> str:
    """生成结构化 Markdown 报告（供 GitHub Issue 使用）。"""
    ctx = _load_report_context(db_path=db_path, generated_at=generated_at)
    now = ctx["now"]

    if ctx["empty"]:
        return (
            "# LOF 套利日报\n\n"
            f"> 生成时间：**{now}**（北京时间）\n\n"
            "## 数据状态\n\n"
            "今日暂无 LOF 行情数据，请检查 ETL 是否正常运行。\n"
        )

    sections = [
        "# LOF 套利日报",
        "",
        *_report_meta_lines(now),
        "",
        "---",
        "",
        "## 一、数据概览",
        "",
        _estimation_summary_md(ctx["df_all"]),
        "---",
        "",
        "## 二、限购高溢价 TOP10",
        "",
        "> **策略提示**：限购 + 高溢价 = 溢价更稳定，优先关注",
        "",
        _fund_table(ctx["df_limited"], show_status=True),
        "---",
        "",
        "## 三、高溢价 TOP10（卖出赎回套利）",
        "",
        "> **策略提示**：场内卖出 + 场外赎回，赚取溢价差",
        "",
        _fund_table(ctx["df_premium"]),
        "---",
        "",
        "## 四、高折价 TOP10（买入套利）",
        "",
        "> **策略提示**：场内买入 + 场外申购，赚取折价差",
        "",
        _fund_table(ctx["df_discount"]),
        "---",
        "",
        "## 五、暂停申购·高溢价 TOP10",
        "",
        "> **策略提示**：场外申购已关闭，**无法新开申购套利**；若已持仓，可关注场内高溢价卖出机会",
        "",
        _fund_table(ctx["df_suspended"], show_status=True),
        "---",
        "",
        "## 六、风险提示",
        "",
        "| 类型 | 说明 |",
        "| --- | --- |",
        "| 交割周期 | LOF 套利 T+2 交割，资金占用约 2 个交易日 |",
        "| 赎回费用 | 持有 ≥7 天通常 0.5%，不足 7 天约 1.5% |",
        "| 流动性 | 高溢价不等于能成交，需关注成交额 |",
        "| 暂停申购 | 第五节品种不可申购套利，仅供已有份额的场内卖出参考 |",
        "| 美股 QDII | **仅「当日溢价」可信**，次日预估不可作为交易依据 |",
        "| 港股 QDII | 当日/次日溢价均可较精准计算 |",
        "",
        "---",
        "",
        f"<sub>由 [LOF Arbiter](https://github.com/SummerSec/LOF_Arbiter) 自动生成 · "
        f"固定页面 [{REPORT_PAGE_URL}]({REPORT_PAGE_URL}) · 标签 `daily-report`</sub>",
        "",
    ]

    return "\n".join(sections)


def generate_report_html(
    db_path: str = "",
    generated_at: Optional[str] = None,
) -> str:
    """生成固定 HTML 日报页面（docs/index.html），风格参考 TradeSmart LOF 溢价表。"""
    ctx = _load_report_context(db_path=db_path, generated_at=generated_at)
    now = ctx["now"]
    sources = (
        f'<a href="{JISILU_LOF_LIST_URL}" target="_blank" rel="noopener">集思录</a> · '
        f'<a href="{LYLOF_ARBITRAGE_URL}" target="_blank" rel="noopener">路远</a> · '
        f'<a href="{TRADESMART_LOF_URL}" target="_blank" rel="noopener">TradeSmart</a> · '
        f'<a href="https://github.com/SummerSec/LOF_Arbiter" target="_blank" rel="noopener">GitHub</a>'
    )

    if ctx["empty"]:
        inner = (
            '<header class="hero">'
            '<div class="hero-top"><h1>LOF 套利日报</h1></div>'
            f'<p class="subtitle">生成时间：{now}（北京时间）</p>'
            "</header>"
            '<div class="card"><p class="empty">今日暂无 LOF 行情数据，请检查 ETL 是否正常运行。</p></div>'
        )
    else:
        fund_count = len(ctx["df_all"])
        inner = (
            '<header class="hero">'
            '<div class="hero-top">'
            "<h1>LOF 套利日报</h1>"
            f'<span class="count-badge">{fund_count} 只基金</span>'
            "</div>"
            '<p class="subtitle">A 股 LOF 折溢价套利机会日报，溢价基于 T-2 锚定 + R(T-1) 估算，非实际净值。</p>'
            f'<p class="sources">生成时间：{now}（北京时间） · 数据来源：LOF Arbiter ETL · 参考：{sources}</p>'
            f"{_estimation_summary_html(ctx['df_all'])}"
            "</header>"
            + _html_section(
                "二、限购高溢价 TOP10",
                "限购 + 高溢价 = 溢价更稳定，优先关注",
                _fund_table_html(ctx["df_limited"], show_status=True),
                len(ctx["df_limited"]),
            )
            + _html_section(
                "三、高溢价 TOP10（卖出赎回套利）",
                "场内卖出 + 场外赎回，赚取溢价差",
                _fund_table_html(ctx["df_premium"]),
                len(ctx["df_premium"]),
            )
            + _html_section(
                "四、高折价 TOP10（买入套利）",
                "场内买入 + 场外申购，赚取折价差",
                _fund_table_html(ctx["df_discount"]),
                len(ctx["df_discount"]),
            )
            + _html_section(
                "五、暂停申购·高溢价 TOP10",
                "场外申购已关闭，无法新开申购套利；若已持仓，可关注场内高溢价卖出机会",
                _fund_table_html(ctx["df_suspended"], show_status=True),
                len(ctx["df_suspended"]),
            )
            + _html_risk_section()
            + (
                f'<p class="footer">由 <a href="https://github.com/SummerSec/LOF_Arbiter">LOF Arbiter</a> 自动生成 · '
                f'固定页面 <a href="{REPORT_PAGE_URL}">{REPORT_PAGE_URL}</a></p>'
            )
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LOF 套利日报 · {now}</title>
  <meta name="description" content="LOF 基金折溢价套利日报，含限购/高溢价/暂停申购/高折价 TOP 榜单。">
  <style>{_HTML_PAGE_STYLES}</style>
</head>
<body>
  <div class="page">
{inner}
  </div>
</body>
</html>
"""


def write_daily_reports(
    db_path: str = "",
    generated_at: Optional[str] = None,
    html_path: str = "docs/index.html",
    md_path: str = "",
) -> Dict[str, str]:
    """写入 Markdown（可选）与固定 HTML 日报。"""
    md = generate_report_markdown(db_path=db_path, generated_at=generated_at)
    html = generate_report_html(db_path=db_path, generated_at=generated_at)

    html_dir = os.path.dirname(html_path)
    if html_dir:
        os.makedirs(html_dir, exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    if md_path:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)

    return {"markdown": md, "html_path": html_path, "md_path": md_path or ""}


def generate_report_text(db_path: str = "") -> str:
    """生成纯文本报告（供邮件使用，由 Markdown 报告简化而来）。"""
    md = generate_report_markdown(db_path=db_path)
    # 粗略去除 Markdown 标记，保留可读纯文本
    plain = md.replace("**", "").replace("_", "")
    for token in ("#", ">", "| --- |", "---"):
        plain = plain.replace(token, "")
    return plain


if __name__ == "__main__":
    paths = write_daily_reports(md_path="")
    report = paths["markdown"]
    print(report)
    print(f"\n[notify] HTML written to {paths['html_path']}")
    print()

    if get_env("SMTP_USER") and get_env("NOTIFY_TO"):
        send_report(generate_report_text())
    else:
        print("[notify] SMTP not configured, report printed only")
