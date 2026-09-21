# -*- coding: utf-8 -*-
import os

class GlobalConst:
    AESKey = "u2oh6Vu^HWe4_AES"
    COOKIES_PATH = os.environ.get("FUCKCOURSE_COOKIES", "cookies.json")

    # 支持按单注入浏览器指纹（WK_UA / WK_PLATFORM / WK_LANG），用于风控实验
    _UA = os.environ.get("WK_UA") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    _PLAT = os.environ.get("WK_PLATFORM") or '"Windows"'
    _LANG = os.environ.get("WK_LANG") or "zh-CN,zh;q=0.9"
    _CHUA = os.environ.get("WK_CHUA") or '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"'

    HEADERS = {
        "User-Agent": _UA,
        "sec-ch-ua": _CHUA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": _PLAT,
        "Accept-Language": _LANG,
    }
    VIDEO_HEADERS = {
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842",
    }
    AUDIO_HEADERS = {
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2025-0725-1842",
    }

    POLL_INTERVAL = 1  # 视频进度轮询间隔（秒）
