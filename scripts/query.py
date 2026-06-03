"""
LOF Arbiter - 数据查询模块

LOF 基金溢价套利机会监测
支持独立数据库，不依赖 DataHub
"""

import sqlite3
import pandas as pd
from datetime import date, timedelta
from typing import Optional, List, Dict
import os

# Skill 数据目录
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(SKILL_DIR, 'data', 'lof_arbiter.db')


def get_connection(db_path: str = DEFAULT_DB_PATH):
    """获取数据库连接"""
    return sqlite3.connect(db_path)


def get_latest_trade_date(db_path: str = DEFAULT_DB_PATH) -> str:
    """获取最近交易日期"""
    conn = get_connection(db_path)
    try:
        c = conn.execute(
            "SELECT MAX(trade_date) FROM lof_daily WHERE trade_date IS NOT NULL"
        )
        row = c.fetchone()
        return row[0] if row and row[0] else date.today().strftime('%Y-%m-%d')
    finally:
        conn.close()


def get_lof_data(
    trade_date: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> pd.DataFrame:
    """
    获取 LOF 基金数据
    """
    if trade_date is None:
        trade_date = get_latest_trade_date(db_path)

    conn = get_connection(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM lof_daily WHERE trade_date = ? ORDER BY turnover DESC",
            conn,
            params=(trade_date,)
        )

        # 成交额格式化（万元）
        if 'turnover' in df.columns:
            df['turnover_wan'] = df['turnover'] / 10000

        # 状态分类
        def classify_status(status):
            if pd.isna(status):
                return 'unknown'
            if '暂停' in str(status):
                return 'suspended'
            if '限大额' in str(status) or '限额' in str(status):
                return 'limited'
            if '开放' in str(status):
                return 'open'
            return 'other'

        df['status_class'] = df['purchase_status'].apply(classify_status)

        return df
    finally:
        conn.close()


def get_premium_top(
    n: int = 10,
    min_premium: float = 0.5,
    min_turnover: float = 1000000,
    db_path: str = DEFAULT_DB_PATH
) -> pd.DataFrame:
    """高溢价 TOP N（卖出赎回套利机会）"""
    df = get_lof_data(db_path=db_path)

    df = df[df['premium_rate'] > min_premium]
    df = df[df['turnover'] >= min_turnover]
    df = df[df['status_class'] != 'suspended']

    df = df.sort_values(['premium_rate', 'status_class'], ascending=[False, True])

    return df.head(n)


def get_discount_top(
    n: int = 10,
    min_discount: float = 0.5,
    min_turnover: float = 1000000,
    db_path: str = DEFAULT_DB_PATH
) -> pd.DataFrame:
    """高折价 TOP N（买入套利机会）"""
    df = get_lof_data(db_path=db_path)

    df = df[df['premium_rate'] < -min_discount]
    df = df[df['turnover'] >= min_turnover]
    df = df[df['status_class'] != 'suspended']

    df = df.sort_values('premium_rate', ascending=True)

    return df.head(n)


def get_limited_premium_top(
    n: int = 10,
    min_premium: float = 0.5,
    min_turnover: float = 1000000,
    db_path: str = DEFAULT_DB_PATH
) -> pd.DataFrame:
    """限购高溢价 TOP N（核心套利机会）"""
    df = get_lof_data(db_path=db_path)

    df = df[df['status_class'] == 'limited']
    df = df[df['premium_rate'] > min_premium]
    df = df[df['turnover'] >= min_turnover]

    df = df.sort_values('premium_rate', ascending=False)

    return df.head(n)


def get_fund_by_code(
    code: str,
    db_path: str = DEFAULT_DB_PATH
) -> Optional[Dict]:
    """根据代码查询基金"""
    code = str(code).strip().upper()
    code_clean = code.replace('.SZ', '').replace('.SH', '').replace('SZ', '').replace('SH', '')

    conn = get_connection(db_path)
    try:
        df = pd.read_sql_query(
            """SELECT * FROM lof_daily
               WHERE fund_code LIKE ? OR fund_code_full LIKE ? OR fund_name LIKE ?
               ORDER BY trade_date DESC LIMIT 1""",
            conn,
            params=(f'%{code_clean}%', f'%{code_clean}%', f'%{code}%')
        )

        if df.empty:
            return None

        return df.iloc[0].to_dict()
    finally:
        conn.close()


def calculate_arb_profit(
    fund_code: str,
    amount: float,
    hold_days: int = 7,
    db_path: str = DEFAULT_DB_PATH
) -> Optional[Dict]:
    """计算套利收益"""
    fund = get_fund_by_code(fund_code, db_path)

    if not fund:
        return None

    purchase_fee_rate = fund.get('fee_rate', 0.012) or 0.012
    redeem_fee_rate = 0.005 if hold_days >= 7 else 0.015
    commission_rate = 0.0003

    try:
        # 优先使用估算净值
        est_nav = fund.get('estimated_nav')
        if est_nav is not None:
            nav = float(est_nav)
            nav_source = 'estimated'
        else:
            nav = float(fund.get('nav')) if fund.get('nav') else 0
            nav_source = 'last_nav'

        price = float(fund.get('price')) if fund.get('price') else nav
        if not nav:
            nav = float(fund.get('prev_nav')) if fund.get('prev_nav') else 0
            price = nav
            nav_source = 'prev_nav'
    except (ValueError, TypeError):
        return None

    if not nav:
        return None

    shares = amount / nav
    purchase_fee = amount * purchase_fee_rate
    sell_amount = shares * price
    redeem_fee = sell_amount * redeem_fee_rate
    commission = sell_amount * commission_rate
    net_profit = sell_amount - amount - purchase_fee - redeem_fee - commission
    net_profit_rate = net_profit / amount * 100

    return {
        'fund_name': fund.get('fund_name'),
        'fund_code': fund.get('fund_code_full'),
        'buy_amount': amount,
        'shares': shares,
        'nav': nav,
        'nav_source': nav_source,
        'nav_date': fund.get('nav_date') or fund.get('prev_nav_date'),
        'price': price,
        'premium_rate': fund.get('premium_rate'),
        'estimated_nav': fund.get('estimated_nav'),
        'estimation_method': fund.get('estimation_method'),
        'purchase_fee': purchase_fee,
        'redeem_fee': redeem_fee,
        'commission': commission,
        'total_fee': purchase_fee + redeem_fee + commission,
        'net_profit': net_profit,
        'net_profit_rate': net_profit_rate,
        'hold_days': hold_days
    }


def export_lof_csv(
    filepath: str,
    min_turnover: float = 1000000,
    db_path: str = DEFAULT_DB_PATH
) -> str:
    """导出 LOF 基金行情 CSV"""
    df = get_lof_data(db_path=db_path)
    df = df[df['turnover'] >= min_turnover * 0.1]

    export_df = pd.DataFrame()
    export_df['基金代码'] = df['fund_code_full']
    export_df['名称'] = df['fund_name']
    export_df['溢价率'] = df['premium_rate'].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else '')
    export_df['当日交易额(万元)'] = df['turnover_wan'].apply(lambda x: f"{x:.2f}" if pd.notna(x) else '')
    export_df['现价'] = df['price'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else '')
    export_df['涨跌幅'] = df['change_pct'].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else '')
    export_df['净值'] = df['nav'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else '')
    export_df['估算净值'] = df['estimated_nav'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else '')
    export_df['跟踪标的涨跌'] = df['benchmark_change_pct'].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else '')
    export_df['汇率变动'] = df['fx_change_pct'].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else '')
    export_df['估算方式'] = df['estimation_method'].fillna('')
    export_df['时间'] = df['nav_date'].fillna(df['prev_nav_date'])
    export_df['申购状态'] = df['purchase_status'].fillna('')
    export_df['购买起点'] = df['purchase_limit'].apply(lambda x: f"{x:.2f}" if pd.notna(x) else '')
    export_df['日累计限定金额'] = df['daily_limit'].apply(lambda x: f"{x:.2f}" if pd.notna(x) else '')
    export_df['手续费'] = df['fee_rate'].apply(lambda x: f"{x:.4f}" if pd.notna(x) else '')

    export_df.to_csv(filepath, index=False, encoding='utf-8-sig')

    return filepath


def format_fund_row(row: Dict, include_status: bool = True) -> str:
    """格式化基金信息为文本"""
    name = row.get('fund_name', '未知')
    code = row.get('fund_code_full', '')
    premium = row.get('premium_rate') or 0
    price = row.get('price') or 0
    nav = row.get('nav') or row.get('prev_nav') or 0
    nav_date = row.get('nav_date') or row.get('prev_nav_date') or ''
    turnover = row.get('turnover') or 0
    turnover_wan = turnover / 10000 if turnover else 0
    status = row.get('purchase_status', '未知')
    est_nav = row.get('estimated_nav')
    bm_change = row.get('benchmark_change_pct')
    fx_change = row.get('fx_change_pct')
    method = row.get('estimation_method', '')

    if premium > 1:
        premium_str = f"🔥 +{premium:.2f}%"
    elif premium < -1:
        premium_str = f"💎 {premium:.2f}%"
    else:
        premium_str = f"{premium:.2f}%"

    if turnover_wan >= 10000:
        turnover_str = f"{turnover_wan/10000:.2f}亿"
    elif turnover_wan >= 1:
        turnover_str = f"{turnover_wan:.2f}万"
    else:
        turnover_str = f"{turnover_wan*10000:.0f}元"

    nav_date_str = f"（净值日期: {nav_date}）" if nav_date else ''

    status_tag = ''
    if include_status:
        if '限大额' in str(status) or '限额' in str(status):
            status_tag = ' [限购]'
        elif '暂停' in str(status):
            status_tag = ' [暂停]'

    lines = [
        f"{name}（{code}）{status_tag}",
        f"  溢价率: {premium_str} | 现价: {price:.3f} | 净值: {nav:.4f} {nav_date_str}",
    ]

    if method == 'TRACKING' and est_nav is not None:
        est_line = f"  估算净值: {est_nav:.4f}"
        if bm_change is not None:
            bm_tag = f"+{bm_change:.2f}%" if bm_change >= 0 else f"{bm_change:.2f}%"
            est_line += f" | 跟踪标的涨跌: {bm_tag}"
        if fx_change is not None:
            fx_tag = f"+{fx_change:.2f}%" if fx_change >= 0 else f"{fx_change:.2f}%"
            est_line += f" | 汇率变动: {fx_tag}"
        lines.append(est_line)
    elif method == 'LEGACY':
        lines.append("  [Legacy] est.NAV unavailable, using last NAV")

    lines.append(f"  成交额: {turnover_str} | 状态: {status}")

    return "\n".join(lines)


def format_arbitrage_report(db_path: str = DEFAULT_DB_PATH) -> str:
    """生成套利机会报告"""
    lines = []

    # 估算方式概览
    df_all = get_lof_data(db_path=db_path)
    if 'estimation_method' in df_all.columns:
        summary = df_all['estimation_method'].value_counts()
        tracking_n = summary.get('TRACKING', 0)
        legacy_n = summary.get('LEGACY', 0)
        total = len(df_all)
        lines.append(f"Estimation: {tracking_n} tracking / {legacy_n} legacy (total {total})")
        lines.append("")

    df_limited = get_limited_premium_top(n=5, min_premium=0.3)
    if not df_limited.empty:
        lines.append("🎯 【限购高溢价 TOP5】（优质套利机会）")
        for _, row in df_limited.iterrows():
            lines.append(format_fund_row(row.to_dict()))
            lines.append("")
    else:
        lines.append("🎯 【限购高溢价】今日暂无满足条件的限购高溢价品种")
        lines.append("")

    df_premium = get_premium_top(n=5, min_premium=0.5)
    if not df_premium.empty:
        lines.append("🔥 【高溢价 TOP5】（卖出赎回套利）")
        for _, row in df_premium.iterrows():
            lines.append(format_fund_row(row.to_dict()))
            lines.append("")

    df_discount = get_discount_top(n=5, min_discount=0.5)
    if not df_discount.empty:
        lines.append("💎 【高折价 TOP5】（买入套利）")
        for _, row in df_discount.iterrows():
            lines.append(format_fund_row(row.to_dict()))
            lines.append("")

    lines.append("⚠️ 风险提示：")
    lines.append("- 套利需 T+2 交割，资金占用两天")
    lines.append("- 赎回费通常 0.5%，持有 <7天 为 1.5%")
    lines.append("- 高溢价需关注流动性，避免无法成交")
    lines.append("- 限购产品溢价更稳定，优先关注")
    lines.append("- 溢价率基于实时估算净值计算，非实际净值")

    return "\n".join(lines)


def has_data(db_path: str = DEFAULT_DB_PATH) -> bool:
    """检查是否有数据"""
    conn = get_connection(db_path)
    try:
        c = conn.execute("SELECT COUNT(*) FROM lof_daily")
        count = c.fetchone()[0]
        return count > 0
    finally:
        conn.close()


if __name__ == '__main__':
    from scripts.db import init_database

    # 初始化数据库
    init_database()

    # 检查是否有数据
    if has_data():
        print("=== LOF Arbiter 测试 ===\n")
        print(format_arbitrage_report())
    else:
        print("数据库暂无数据，请先运行 ETL：")
        print("  python -m scripts.etl")
