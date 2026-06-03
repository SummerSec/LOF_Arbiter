"""
LOF Arbiter - 邮件通知模块

发送套利机会报告到指定邮箱。
支持 QQ邮箱 / 163邮箱 / Gmail 等 SMTP 服务。

环境变量配置：
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
    """
    发送套利报告邮件。

    Parameters
    ----------
    report_text : str
        报告正文（纯文本）
    to_addrs : list, optional
        收件人列表，默认从 NOTIFY_TO 环境变量读取
    """
    # 读取配置
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
    subject = f"[{subject_prefix}] LOF Arbitrage Report - {now}" if subject_prefix else f"LOF Arbitrage Report - {now}"

    # 构建邮件
    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject

    body = f"""
LOF Arbitrage Report
{'=' * 60}
Generated: {now}

{report_text}

{'=' * 60}
Sent by LOF Arbiter Bot
"""
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        if port == 465:
            # SSL 直连
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(user, password)
                server.sendmail(user, to_list, msg.as_string())
        else:
            # STARTTLS
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                server.login(user, password)
                server.sendmail(user, to_list, msg.as_string())

        print(f"[notify] Report sent to {len(to_list)} recipient(s)")
        return True
    except Exception as e:
        print(f"[notify] Failed to send: {e}")
        return False


def generate_report_text(db_path: str = "") -> str:
    """生成套利报告文本（供邮件使用，不含 emoji）"""
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from scripts.query import (
        get_lof_data,
        get_premium_top,
        get_discount_top,
        get_limited_premium_top,
        DEFAULT_DB_PATH,
    )

    db = db_path or DEFAULT_DB_PATH
    lines = []

    df_all = get_lof_data(db_path=db)
    if df_all.empty:
        return "No LOF data available today."

    # 估算方式概览
    if "estimation_method" in df_all.columns:
        summary = df_all["estimation_method"].value_counts()
        tracking_n = summary.get("TRACKING", 0)
        legacy_n = summary.get("LEGACY", 0)
        lines.append(f"Estimation: {tracking_n} tracking / {legacy_n} legacy (total {len(df_all)})")
        lines.append("")

    # 限购高溢价
    df_limited = get_limited_premium_top(n=5, min_premium=0.3)
    if not df_limited.empty:
        lines.append("-- Limited+Premium TOP5 --")
        for _, row in df_limited.iterrows():
            name = row.get("fund_name", "")
            code = row.get("fund_code_full", "")
            premium = row.get("premium_rate") or 0
            price = row.get("price") or 0
            nav = row.get("nav") or row.get("prev_nav") or 0
            turnover = (row.get("turnover") or 0) / 10000
            status = row.get("purchase_status", "")
            lines.append(f"  {name}({code}) premium={premium:+.2f}% price={price:.3f} nav={nav:.4f} turnover={turnover:.1f}w status={status}")
        lines.append("")

    # 高溢价
    df_premium = get_premium_top(n=5, min_premium=0.5)
    if not df_premium.empty:
        lines.append("-- Premium TOP5 (sell/redeem) --")
        for _, row in df_premium.iterrows():
            name = row.get("fund_name", "")
            code = row.get("fund_code_full", "")
            premium = row.get("premium_rate") or 0
            price = row.get("price") or 0
            nav = row.get("nav") or row.get("prev_nav") or 0
            turnover = (row.get("turnover") or 0) / 10000
            lines.append(f"  {name}({code}) premium={premium:+.2f}% price={price:.3f} nav={nav:.4f} turnover={turnover:.1f}w")
        lines.append("")

    # 高折价
    df_discount = get_discount_top(n=5, min_discount=0.5)
    if not df_discount.empty:
        lines.append("-- Discount TOP5 (buy arbitrage) --")
        for _, row in df_discount.iterrows():
            name = row.get("fund_name", "")
            code = row.get("fund_code_full", "")
            premium = row.get("premium_rate") or 0
            price = row.get("price") or 0
            nav = row.get("nav") or row.get("prev_nav") or 0
            turnover = (row.get("turnover") or 0) / 10000
            lines.append(f"  {name}({code}) premium={premium:+.2f}% price={price:.3f} nav={nav:.4f} turnover={turnover:.1f}w")
        lines.append("")

    lines.append("---")
    lines.append("Risks: T+2 settlement, redemption fee 0.5% (1.5% if <7 days), liquidity risk")
    lines.append("Premium rates based on real-time estimated NAV")

    return "\n".join(lines)


if __name__ == "__main__":
    report = generate_report_text()
    print(report)
    print()

    if get_env("SMTP_USER") and get_env("NOTIFY_TO"):
        send_report(report)
    else:
        print("[notify] SMTP not configured, report printed only")
