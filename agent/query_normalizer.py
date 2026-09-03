# agent/query_normalizer.py
"""Query Normalizer — 对用户输入进行预处理，增强事实搜索的准确性。

在 user_input 进入 planner 之前执行：
1. 时间归一化：今天/昨天/明天/本周/下周 → 实际日期
2. 地区归一化：天气/附近/本地类查询 → 注入用户所在地
"""
import re
from datetime import datetime, timedelta


class QueryNormalizer:
    """Normalize user queries by injecting time and location context."""

    # Keywords that require location context
    LOCATION_QUERY_SIGNALS = [
        "天气", "温度", "气温", "下雨", "下雪", "台风", "雾霾",
        "pm2.5", "空气", "湿度",
        "附近", "周边", "本地", "当地",
        "快递", "外卖", "打车", "公交", "地铁", "路况",
        "电影", "上映", "影院", "餐厅", "美食",
    ]

    @staticmethod
    def process(
        user_input: str,
        user_id: str = "",
        *,
        scope: str = "user",
    ) -> str:
        """Process user input: normalize time references and inject location.

        Args:
            user_input: Original user input string
            user_id: User identifier (for looking up location from facts)

        Returns:
            Normalized input string with time/date references replaced.
        """
        result = user_input

        # Step 1: Normalize time references
        result = QueryNormalizer._normalize_time(result)

        # Step 2: Inject location for location-sensitive queries
        result = QueryNormalizer._inject_location(result, user_id, scope=scope)

        return result

    @staticmethod
    def _normalize_time(text: str) -> str:
        """Replace relative time references with absolute dates."""
        now = datetime.now()
        today = now.strftime("%Y年%m月%d日")
        yesterday = (now - timedelta(days=1)).strftime("%Y年%m月%d日")
        tomorrow = (now + timedelta(days=1)).strftime("%Y年%m月%d日")
        day_before = (now - timedelta(days=2)).strftime("%Y年%m月%d日")
        day_after = (now + timedelta(days=2)).strftime("%Y年%m月%d日")
        this_month = now.strftime("%Y年%m月")
        this_year = now.strftime("%Y年")
        week_num = now.isocalendar()[1]
        last_week = f"{now.year}年第{week_num - 1}周"
        next_week = f"{now.year}年第{week_num + 1}周"

        # Direct string replacement for Chinese time words
        text = text.replace("前天", day_before)
        text = text.replace("后天", day_after)
        text = text.replace("昨天", yesterday)
        text = text.replace("明天", tomorrow)
        text = text.replace("今天", today)
        text = text.replace("今日", today)
        text = text.replace("昨日", yesterday)
        text = text.replace("明日", tomorrow)
        text = text.replace("本周", f"{now.year}年第{week_num}周")
        text = text.replace("这周", f"{now.year}年第{week_num}周")
        text = text.replace("上周", last_week)
        text = text.replace("下周", next_week)
        text = text.replace("本月", this_month)
        text = text.replace("这个月", this_month)
        text = text.replace("今年", this_year)

        return text

    @staticmethod
    def _inject_location(text: str, user_id: str, *, scope: str = "user") -> str:
        """Inject user location into location-sensitive queries."""
        if not user_id:
            return text

        text_lower = text.lower()
        needs_location = any(signal in text_lower for signal in QueryNormalizer.LOCATION_QUERY_SIGNALS)

        if not needs_location:
            return text

        location = QueryNormalizer._get_user_location(user_id, scope=scope)
        if not location:
            return text

        loc_lower = location.lower()
        if loc_lower in text_lower:
            return text

        return f"{location} {text}"

    @staticmethod
    def _get_user_location(user_id: str, *, scope: str = "user") -> str:
        """Get user's location from the facts store."""
        try:
            from agent.memory.long_term import get_facts
            facts = get_facts(user_id, scope=scope)
            if not facts:
                return ""

            for category in ("personal", "misc", "preferences"):
                cat = facts.get(category, {})
                if not cat:
                    continue
                for key in ("location", "city", "area", "address", "region"):
                    val = cat.get(key, "")
                    if val and val.strip():
                        return val.strip()
            return ""
        except Exception:
            return ""
