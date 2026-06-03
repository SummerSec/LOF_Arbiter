"""
LOF Arbiter - Jisilu Data Source

Encapsulates Jisilu (JS) API for LOF fund historical data.
API: GET https://www.jisilu.cn/data/lof/hist_list/{code}
No authentication required.
"""

import time
import requests
import pandas as pd
from typing import Optional, List, Dict
from datetime import datetime

from scripts.db import init_database, save_jisilu_data, get_connection

JISILU_BASE = "https://www.jisilu.cn/data/lof/hist_list"
JISILU_LOF_LIST_URL = "https://www.jisilu.cn/data/lof/"
JISILU_DETAIL_BASE = "https://www.jisilu.cn/data/lof/detail"
DEFAULT_PARAMS = {"___jsl": "LST___t", "rp": "50", "page": "1"}
REQUEST_DELAY = 0.5  # seconds between requests to avoid rate limiting


def clean_fund_code(fund_code: str) -> str:
    """提取 6 位基金代码，供集思录链接等使用。"""
    code = str(fund_code or "").strip().upper()
    for token in (".SZ", ".SH", "SZ", "SH", "."):
        code = code.replace(token, "")
    return code


def jisilu_detail_url(fund_code: str) -> str:
    """集思录 LOF 详情页 URL，例如 https://www.jisilu.cn/data/lof/detail/162411"""
    return f"{JISILU_DETAIL_BASE}/{clean_fund_code(fund_code)}"

# Common LOF codes from lof-arbitrage's all_LOF.txt
DEFAULT_CODES = [
    "163418", "169101", "501082", "160421", "161232", "160135", "163113",
    "161233", "160140", "161029", "161130", "161128", "161125", "161127",
    "164906", "164701", "160719", "160416", "162411", "162719", "501018",
    "160723", "161226", "163208", "161725", "161726", "161628", "161027",
    "161028", "167301", "160633", "501305", "501300", "160631", "160632",
    "160643", "164824", "161907", "160620", "161121", "161122", "161123",
    "164403", "164401", "501029", "501030", "501031", "501025", "161039",
    "161040", "161831", "501016", "501017", "501018", "501019", "160213",
    "160216", "160217", "160218", "160220", "160222", "160225", "160226",
    "161129", "161131", "161132", "161133", "161726", "161727", "161728",
    "161729", "161810", "161811", "161812", "161813", "161815", "161816",
    "161907", "161908", "161909", "161910", "161911", "161912", "161913",
    "162605", "162606", "162607", "162703", "162711", "162712", "165309",
    "165310", "165311", "165312", "165313", "165508", "165509", "165510",
    "165511", "165512", "165513", "165514", "165515", "165516", "165517",
    "165518", "165519", "165520", "165521", "165522", "165523", "165524",
    "165525", "166001", "166002", "166003", "166004", "166005", "166006",
    "166007", "166008", "166009", "166010", "166011", "166012", "166013",
    "166014", "166015", "166016", "167001", "167002", "167003", "167301",
    "167302", "168101", "168102", "168103", "168104", "168105", "168201",
    "168202", "168203", "168204", "168205", "168206", "168207", "168301",
    "168401", "168402", "169101", "169102", "169103", "169104", "169105",
    "169106", "169107", "169108", "169201", "169301", "501001", "501002",
    "501003", "501004", "501005", "501006", "501007", "501008", "501009",
    "501010", "501011", "501012", "501013", "501014", "501015", "501016",
    "501017", "501018", "501019", "501020", "501021", "501022", "501023",
    "501025", "501026", "501027", "501028", "501029", "501030", "501031",
    "501043", "501045", "501046", "501047", "501048", "501049", "501050",
    "501051", "501052", "501053", "501054", "501055", "501056", "501057",
    "501058", "501059", "501060", "501061", "501062", "501063", "501064",
    "501065", "501066", "501067", "501068", "501069", "501070", "501071",
    "501072", "501073", "501075", "501076", "501077", "501078", "501079",
    "501080", "501081", "501082", "501083", "501085", "501086", "501087",
    "501088", "501089", "501090", "501091", "501092", "501093", "501094",
    "501095", "501096", "501097", "501098", "501099", "501100",
]


class JisiluClient:
    """Jisilu API client for LOF fund data."""

    def __init__(self, delay: float = REQUEST_DELAY):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.jisilu.cn/",
        })
        self.delay = delay
        self.base_url = JISILU_BASE

    def fetch_lof_hist(self, code: str) -> Optional[pd.DataFrame]:
        """
        Fetch historical LOF data for a single fund code.

        Returns DataFrame with columns:
        price_dt, price, net_value_dt, net_value, discount_rt, amount, est_val
        """
        url = f"{self.base_url}/{code}"
        try:
            resp = self.session.get(url, params=DEFAULT_PARAMS, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            rows = data.get("rows", [])
            if not rows:
                print(f"[jisilu] {code}: no data returned")
                return None

            df = pd.DataFrame(rows)
            # Extract cell data (Jisilu wraps values in 'cell' dict)
            if "cell" in df.columns:
                cells = pd.DataFrame(df["cell"].tolist())
                df = pd.concat([df.drop(columns=["cell"]), cells], axis=1)

            # Normalize column names
            col_map = {
                "fund_id": "fund_id",
                "price_dt": "price_dt",
                "price": "price",
                "net_value_dt": "net_value_dt",
                "net_value": "net_value",
                "discount_rt": "discount_rt",
                "amount": "amount",
                "est_val": "est_val",
            }
            # Keep only known columns
            available = [c for c in col_map if c in df.columns]
            result = df[available].copy()
            if "fund_id" in result.columns:
                result.rename(columns={"fund_id": "fund_code"}, inplace=True)
            result["fund_code"] = str(code)

            return result
        except Exception as e:
            print(f"[jisilu] {code}: fetch failed ({e})")
            return None

    def fetch_codes(self) -> List[str]:
        """Try to get all available LOF codes from Jisilu."""
        return DEFAULT_CODES

    def sync_all(
        self,
        codes: Optional[List[str]] = None,
        db_path: str = "data/lof_arbiter.db",
    ) -> Dict:
        """
        Sync all LOF fund data from Jisilu to local SQLite.

        Returns summary dict with updated / no_data / failed counts.
        """
        if codes is None:
            codes = self.fetch_codes()

        init_database(db_path)
        updated, no_data, failed = 0, 0, 0

        for i, code in enumerate(codes):
            if i > 0:
                time.sleep(self.delay)

            df = self.fetch_lof_hist(code)
            if df is None or df.empty:
                no_data += 1
                continue

            try:
                records = []
                for _, row in df.iterrows():
                    records.append({
                        "fund_code": str(code),
                        "price_dt": row.get("price_dt"),
                        "price": _safe_float(row.get("price")),
                        "net_value_dt": row.get("net_value_dt"),
                        "net_value": _safe_float(row.get("net_value")),
                        "discount_rt": _safe_float(row.get("discount_rt")),
                        "est_val": _safe_float(row.get("est_val")),
                        "amount": _safe_float(row.get("amount")),
                    })
                n = save_jisilu_data(records, db_path)
                updated += 1
                if (i + 1) % 20 == 0:
                    print(f"[jisilu] synced {i + 1}/{len(codes)}...")
            except Exception as e:
                print(f"[jisilu] {code}: save failed ({e})")
                failed += 1

        print(f"[jisilu] sync complete: {updated} updated, "
              f"{no_data} no data, {failed} failed")
        return {"updated": updated, "no_data": no_data, "failed": failed}


def get_jisilu_data(
    code: str,
    db_path: str = "data/lof_arbiter.db",
) -> Optional[pd.DataFrame]:
    """Read Jisilu data from SQLite for a single LOF code."""
    conn = get_connection(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM lof_jisilu WHERE fund_code = ? ORDER BY price_dt DESC",
            conn,
            params=(str(code),),
        )
        return df if not df.empty else None
    finally:
        conn.close()


def get_jisilu_latest(
    db_path: str = "data/lof_arbiter.db",
) -> pd.DataFrame:
    """Get latest Jisilu record for each LOF code."""
    conn = get_connection(db_path)
    try:
        df = pd.read_sql_query(
            """SELECT * FROM lof_jisilu
               WHERE (fund_code, price_dt) IN (
                   SELECT fund_code, MAX(price_dt)
                   FROM lof_jisilu GROUP BY fund_code
               )
               ORDER BY CAST(discount_rt AS REAL) DESC""",
            conn,
        )
        return df
    finally:
        conn.close()


def _safe_float(val) -> Optional[float]:
    """Safely convert to float, handling '-' and NaN."""
    if val is None:
        return None
    try:
        if isinstance(val, str) and val.strip() in ("", "-", "--", "nan", "NaN"):
            return None
        v = float(val)
        if pd.isna(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    import sys

    client = JisiluClient()
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        code = sys.argv[2] if len(sys.argv) > 2 else "162411"
        print(f"Testing Jisilu API for {code}...")
        df = client.fetch_lof_hist(code)
        if df is not None:
            print(f"Retrieved {len(df)} records")
            print(df.head().to_string())
            print(f"\nColumns: {list(df.columns)}")
        else:
            print("No data returned")
    else:
        print("Syncing all LOF data from Jisilu...")
        result = client.sync_all()
        print(f"Done: {result}")
