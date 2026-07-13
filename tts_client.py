import requests
import base64
import re
import json
import os
import time
from datetime import datetime

# ================== 翻译配置 ==================
# 使用免费翻译API（无需API Key，但限制频率）
FREE_TRANSLATE_API = "https://api.mymemory.translated.net/get"

# 备用：百度翻译（需要API Key，免费额度100万字符/月）
BAIDU_APPID = os.getenv("BAIDU_TRANSLATE_APPID", "")
BAIDU_SECRET = os.getenv("BAIDU_TRANSLATE_SECRET", "")
BAIDU_API = "https://fanyi-api.baidu.com/api/trans/vip/translate"

def translate_to_japanese(text: str) -> str:
    """将中文翻译成日语"""
    # [时间] 开始翻译
    _start = time.time()
    print(f"[时间] translate_to_japanese 开始，时间：{datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

    if not text:
        print(f"[时间] translate_to_japanese 结束（空文本），耗时：{(time.time()-_start)*1000:.2f}ms")
        return ""

    # 先清理动作描述（括号内容）
    clean_text = clean_text_for_tts(text)
    if not clean_text:
        print(f"[时间] translate_to_japanese 结束（清理后为空），耗时：{(time.time()-_start)*1000:.2f}ms")
        return ""

    # 如果文本已经包含日语假名，直接返回
    if re.search(r'[\u3040-\u30FF]', clean_text):
        print(f"[时间] translate_to_japanese 结束（已是日语），耗时：{(time.time()-_start)*1000:.2f}ms")
        return clean_text

    # 方案一：使用免费API（MyMemory）
    try:
        _t1 = time.time()
        params = {
            "q": clean_text,
            "langpair": "zh|ja"
        }
        resp = requests.get(FREE_TRANSLATE_API, params=params, timeout=10)
        data = resp.json()
        if data.get("responseStatus") == 200:
            translated = data.get("responseData", {}).get("translatedText", "")
            print(f"[翻译] 成功: {clean_text}... → {translated}...")
            _t2 = time.time()
            print(f"[时间] 免费API翻译耗时：{(_t2-_t1)*1000:.2f}ms")
            print(f"[时间] translate_to_japanese 结束（免费API），总耗时：{(time.time()-_start)*1000:.2f}ms")
            return translated
    except Exception as e:
        print(f"[翻译] 免费API失败: {e}")

    # 方案二：使用百度翻译（更稳定）
    if BAIDU_APPID and BAIDU_SECRET:
        try:
            _t1 = time.time()
            import hashlib
            salt = str(int(time.time()))
            sign_str = BAIDU_APPID + clean_text + salt + BAIDU_SECRET
            sign = hashlib.md5(sign_str.encode()).hexdigest()
            params = {
                "q": clean_text,
                "from": "zh",
                "to": "jp",
                "appid": BAIDU_APPID,
                "salt": salt,
                "sign": sign
            }
            resp = requests.get(BAIDU_API, params=params, timeout=10)
            data = resp.json()
            if "trans_result" in data:
                translated = data["trans_result"][0]["dst"]
                print(f"[翻译] 百度翻译成功: {clean_text[:30]}... → {translated[:30]}...")
                _t2 = time.time()
                print(f"[时间] 百度翻译API耗时：{(_t2-_t1)*1000:.2f}ms")
                print(f"[时间] translate_to_japanese 结束（百度API），总耗时：{(time.time()-_start)*1000:.2f}ms")
                return translated
        except Exception as e:
            print(f"[翻译] 百度翻译失败: {e}")

    # 翻译失败，返回原文本（降级）
    print(f"[翻译] 所有翻译方案失败，使用原文")
    print(f"[时间] translate_to_japanese 结束（降级），总耗时：{(time.time()-_start)*1000:.2f}ms")
    return clean_text

# TTS 服务地址（整合包启动的 api.py）
TTS_API_URL = "http://127.0.0.1:9880/"

def clean_text_for_tts(text: str) -> str:
    """
    移除括号内的动作描述，只保留纯对话文本
    例: "呐呐！（挥手）今天天气真好！" → "呐呐！今天天气真好！"
    """
    # 移除中文括号及其内容
    text = re.sub(r'[（(][^）)]*[）)]', '', text)
    # 移除星号包裹的内容
    text = re.sub(r'\*.*?\*', '', text)
    # 清理多余空格
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def text_to_speech(text: str, language: str = "ja", translate: bool = True) -> bytes:
    """
    将文本转为语音
    - text: 输入文本（通常为中文）
    - language: 合成语言（zh/ja/en），默认日语
    - translate: 是否将中文翻译成日语
    """
    # [时间] 开始 TTS
    _start = time.time()
    print(f"[时间] text_to_speech 开始，时间：{datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

    if not text or len(text.strip()) == 0:
        print(f"[时间] text_to_speech 结束（空文本），耗时：{(time.time()-_start)*1000:.2f}ms")
        return None

    # 如果启用翻译，将中文翻译成日语
    if translate and language == "ja":
        _t1 = time.time()
        tts_text = translate_to_japanese(text)
        _t2 = time.time()
        print(f"[时间] 翻译耗时（含清理）：{(_t2-_t1)*1000:.2f}ms")
    else:
        _t1 = time.time()
        tts_text = clean_text_for_tts(text)
        _t2 = time.time()
        print(f"[时间] 清理文本耗时：{(_t2-_t1)*1000:.2f}ms")

    if not tts_text:
        print(f"[时间] text_to_speech 结束（翻译/清理后为空），总耗时：{(time.time()-_start)*1000:.2f}ms")
        return None

    try:
        _t3 = time.time()
        resp = requests.get(
            TTS_API_URL,
            params={
                "text": tts_text,
                "text_language": "ja",      # 固定为日语
                "speed_factor": 1.2,
                "temperature": 0.8,
            },
            timeout=30
        )
        _t4 = time.time()
        print(f"[时间] TTS HTTP 请求耗时：{(_t4-_t3)*1000:.2f}ms")
        if resp.status_code == 200:
            print(f"[TTS] 合成成功，日语文本: {tts_text[:30]}...")
            print(f"[时间] text_to_speech 结束（成功），总耗时：{(time.time()-_start)*1000:.2f}ms")
            return resp.content
        else:
            print(f"[TTS] 合成失败: {resp.status_code}")
            print(f"[时间] text_to_speech 结束（失败），总耗时：{(time.time()-_start)*1000:.2f}ms")
            return None
    except Exception as e:
        print(f"[TTS] 异常: {e}")
        print(f"[时间] text_to_speech 结束（异常），总耗时：{(time.time()-_start)*1000:.2f}ms")
        return None

def detect_language(text: str) -> str:
    """简单检测文本语言，返回 zh 或 ja"""
    # 检测是否包含日语假名
    if re.search(r'[\u3040-\u30FF]', text):
        return "ja"
    return "zh"