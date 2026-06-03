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
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional, List

import pandas as pd


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


def _fund_table(df: pd.DataFrame, show_status: bool = False) -> str:
    if df.empty:
        return "_今日暂无满足条件的品种_\n"

    headers = ["基金", "代码", "当日溢价", "现价", "预估净值", "成交额", "置信度"]
    if show_status:
        headers.insert(6, "申购状态")

    rows = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]

    for _, row in df.iterrows():
        est_nav = row.get("estimated_nav") or row.get("nav") or row.get("prev_nav")
        est_str = f"{est_nav:.4f}" if est_nav is not None and not pd.isna(est_nav) else "—"
        cells = [
            str(row.get("fund_name", "")),
            str(row.get("fund_code_full", "")),
            _format_premium_md(row.get("premium_rate")),
            f"{row.get('price', 0):.3f}" if row.get("price") else "—",
            est_str,
            _format_turnover(row.get("turnover")),
        ]
        if show_status:
            cells.append(_status_badge(row.get("purchase_status", "")))
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
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from scripts.query import (
        get_lof_data,
        get_premium_top,
        get_discount_top,
        get_limited_premium_top,
        DEFAULT_DB_PATH,
    )

    db = db_path or DEFAULT_DB_PATH
    now = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")

    df_all = get_lof_data(db_path=db)
    if df_all.empty:
        return (
            "# LOF 套利日报\n\n"
            f"> 生成时间：**{now}**（北京时间）\n\n"
            "## 数据状态\n\n"
            "今日暂无 LOF 行情数据，请检查 ETL 是否正常运行。\n"
        )

    df_limited = get_limited_premium_top(n=5, min_premium=0.3)
    df_premium = get_premium_top(n=5, min_premium=0.5)
    df_discount = get_discount_top(n=5, min_discount=0.5)

    sections = [
        "# LOF 套利日报",
        "",
        f"> **生成时间**：{now}（北京时间）  ",
        "> **数据来源**：LOF Arbiter 自动 ETL  ",
        "> **溢价说明**：基于 T-2 锚定 + R(T-1) 估算，非实际净值",
        "",
        "---",
        "",
        "## 一、数据概览",
        "",
        _estimation_summary_md(df_all),
        "---",
        "",
        "## 二、限购高溢价 TOP5",
        "",
        "> **策略提示**：限购 + 高溢价 = 溢价更稳定，优先关注",
        "",
        _fund_table(df_limited, show_status=True),
        "---",
        "",
        "## 三、高溢价 TOP5（卖出赎回套利）",
        "",
        "> **策略提示**：场内卖出 + 场外赎回，赚取溢价差",
        "",
        _fund_table(df_premium),
        "---",
        "",
        "## 四、高折价 TOP5（买入套利）",
        "",
        "> **策略提示**：场内买入 + 场外申购，赚取折价差",
        "",
        _fund_table(df_discount),
        "---",
        "",
        "## 五、风险提示",
        "",
        "| 类型 | 说明 |",
        "| --- | --- |",
        "| 交割周期 | LOF 套利 T+2 交割，资金占用约 2 个交易日 |",
        "| 赎回费用 | 持有 ≥7 天通常 0.5%，不足 7 天约 1.5% |",
        "| 流动性 | 高溢价不等于能成交，需关注成交额 |",
        "| 美股 QDII | **仅「当日溢价」可信**，次日预估不可作为交易依据 |",
        "| 港股 QDII | 当日/次日溢价均可较精准计算 |",
        "",
        "---",
        "",
        "<sub>由 [LOF Arbiter](https://github.com/SummerSec/LOF_Arbiter) 自动生成 · 标签 `daily-report`</sub>",
        "",
    ]

    return "\n".join(sections)


def generate_report_text(db_path: str = "") -> str:
    """生成纯文本报告（供邮件使用，由 Markdown 报告简化而来）。"""
    md = generate_report_markdown(db_path=db_path)
    # 粗略去除 Markdown 标记，保留可读纯文本
    plain = md.replace("**", "").replace("_", "")
    for token in ("#", ">", "| --- |", "---"):
        plain = plain.replace(token, "")
    return plain


if __name__ == "__main__":
    report = generate_report_markdown()
    print(report)
    print()

    if get_env("SMTP_USER") and get_env("NOTIFY_TO"):
        send_report(generate_report_text())
    else:
        print("[notify] SMTP not configured, report printed only")
