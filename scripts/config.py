"""
LOF Arbiter - 配置加载与校验模块

加载 fund_tracking_config.json，提供 FundTrackingConfig 数据结构
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional, Dict


SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(SKILL_DIR, "data", "fund_tracking_config.json")


@dataclass
class TrackingTarget:
    """跟踪标的信息"""
    type: str              # domestic_index / global_index / us_etf / domestic_future
    symbol: str            # 标的代码
    name: str              # 标的名称
    source_func: str       # akshare 函数名
    source_filter: str     # DataFrame 中用于过滤的列名 (code / name / symbol)
    source_func_arg: Optional[str] = None  # akshare 函数参数（可选）


@dataclass
class FundTrackingConfig:
    """单只基金的跟踪配置"""
    fund_code: str                     # 6 位基金代码
    fund_name: str                     # 基金简称
    fund_type: str                     # DOMESTIC / QDII / COMMODITY
    tracking_target: TrackingTarget    # 跟踪标的
    position_ratio: float = 1.0        # 仓位比例 (0-1)
    currency_pair: Optional[str] = None  # 汇率对（QDII 专用）
    nav_lag_days: int = 0              # 净值滞后天数


def load_tracking_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, FundTrackingConfig]:
    """加载并校验基金跟踪配置"""
    if not os.path.exists(config_path):
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    raw_funds = raw.get("funds", {})
    if not isinstance(raw_funds, dict):
        return {}

    configs: Dict[str, FundTrackingConfig] = {}
    for code, entry in raw_funds.items():
        try:
            target_raw = entry["tracking_target"]
            target = TrackingTarget(
                type=target_raw["type"],
                symbol=target_raw["symbol"],
                name=target_raw["name"],
                source_func=target_raw["source_func"],
                source_filter=target_raw["source_filter"],
                source_func_arg=target_raw.get("source_func_arg"),
            )
            cfg = FundTrackingConfig(
                fund_code=code,
                fund_name=entry.get("fund_name", ""),
                fund_type=entry.get("fund_type", "DOMESTIC"),
                tracking_target=target,
                position_ratio=entry.get("position_ratio", 1.0),
                currency_pair=entry.get("currency_pair"),
                nav_lag_days=entry.get("nav_lag_days", 0),
            )
            configs[code] = cfg
        except (KeyError, TypeError) as e:
            print(f"[config] 跳过基金 {code}: 配置格式错误 ({e})")

    return configs


def get_fund_config(
    fund_code: str,
    configs: Dict[str, FundTrackingConfig]
) -> Optional[FundTrackingConfig]:
    """根据基金代码查找配置"""
    code = str(fund_code).strip().upper()
    code_clean = code.replace(".SZ", "").replace(".SH", "").replace("SZ", "").replace("SH", "")
    config = configs.get(code_clean)
    if not config:
        config = configs.get(code)
    return config


def is_qdii(code: str, configs: Dict[str, FundTrackingConfig]) -> bool:
    cfg = get_fund_config(code, configs)
    return cfg is not None and cfg.fund_type == "QDII"


def is_domestic(code: str, configs: Dict[str, FundTrackingConfig]) -> bool:
    cfg = get_fund_config(code, configs)
    return cfg is not None and cfg.fund_type == "DOMESTIC"


def get_currency_pairs(configs: Dict[str, FundTrackingConfig]) -> set:
    """获取所有 QDII 基金需要的汇率对"""
    pairs = set()
    for cfg in configs.values():
        if cfg.currency_pair:
            pairs.add(cfg.currency_pair)
    return pairs


def count_by_type(configs: Dict[str, FundTrackingConfig]) -> Dict[str, int]:
    """按基金类型统计"""
    counts: Dict[str, int] = {}
    for cfg in configs.values():
        counts[cfg.fund_type] = counts.get(cfg.fund_type, 0) + 1
    return counts
