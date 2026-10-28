# -*- coding: utf-8 -*-
"""
TeamsSub 翻译子系统
===================
职责边界（与 UI 完全解耦）：
  1. 语言判定与目标语言决策      detect_lang / decide_target
  2. 服务端 token 用量与缓存命中统计
  3. 引擎适配（OpenAI 兼容 / DeepL / Azure）  Provider 家族
  4. 批处理调度 + 重试 + 降级     TranslationService

调用方只需：svc.submit(rec_id, text) → 结果通过 on_result 回调返回。
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

def _data_home() -> Path:
    """配置放哪：冻结成 exe 时跟 exe 走，源码运行时跟脚本走。

    单文件 exe 的 ``__file__`` 在临时解压目录里（退出即删），照它存配置等于
    每次启动都恢复默认设置。
    """
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _data_home()
DATA_DIR = Path(os.environ.get("TEAMSSUB_DATA_DIR", APP_DIR))
CONFIG_PATH = DATA_DIR / "appconfig.json"

# ---------------------------------------------------------------------------
# 凭据保护
# ---------------------------------------------------------------------------
# 配置文件是明文 JSON，API Key 原样写进去就等于把账号借给任何能读到它的程序
# （同步盘、自动备份、误发的日志都算）。用 Windows DPAPI 加密：密钥与当前
# 登录用户绑定，换用户/换机器解不开，正好符合「这份配置只属于这台机器」。
SECRET_FIELDS = ("api_key", "fallback_api_key")
_DPAPI_PREFIX = "dpapi:"


def _protect(text: str) -> str:
    """加密一个密钥字段；环境不支持时原样返回（宁可可用，不要丢配置）。"""
    try:
        import base64
        import win32crypt
        blob = win32crypt.CryptProtectData(
            text.encode("utf-8"), "TeamsSub", None, None, None, 0)
        return _DPAPI_PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception:
        return text


def _unprotect(text):
    """解密；旧配置里的明文（没有前缀）原样返回，下次保存自动转为密文。"""
    if not isinstance(text, str) or not text.startswith(_DPAPI_PREFIX):
        return text
    try:
        import base64
        import win32crypt
        blob = base64.b64decode(text[len(_DPAPI_PREFIX):])
        _, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data.decode("utf-8")
    except Exception:
        # 换了用户或换了机器：解不开就当没填，让用户重新输入，不要带着空密钥
        # 去请求接口然后报一堆看不懂的 401。
        return ""


# ---------------------------------------------------------------- 语境主题
# 主题 = 领域提示 + 领域术语表。固定内容置于系统提示词中。
TOPICS = {
    "general": {
        "name": "通用会议",
        "brief": "通用商务/技术会议，中英混说，口语化，有重复和口头语。",
        "glossary": "",
    },
    "auto_styling": {
        "name": "汽车造型与内饰",
        # 主题说明只写「模型看不出来的东西」：这是什么会、什么行业习惯。
        # 具体译名放到 glossary —— 两处都写会互相打架，也白花 token。
        "brief": (
            "汽车造型设计与内外饰工程评审（主机厂与供应商对接场景）。"
            "常见对象：车身覆盖件与内外饰零件、曲面质量、间隙/面差、模具可行性、总布置。"
            "译名遵循中国主机厂工程口语习惯。"
        ),
        "glossary": (
            "class-A surface=A 面\n"
            "door trim=门板\n"
            "instrument panel=仪表板\n"
            "console=副仪表板\n"
            "headliner=顶棚\n"
            "gap=间隙\n"
            "flush=面差\n"
            "draft angle=拔模角\n"
            "datum=基准\n"
            "section=断面\n"
            "grain=皮纹\n"
            "clip=卡扣"
        ),
    },
    "mechanical": {
        "name": "机械与制造",
        "brief": "机械设计、制造工艺、模具、装配与质量。术语按机械行业通用译法。",
        "glossary": "fixture=夹具\njig=工装\nmachining=机加工\nassembly=装配",
    },
    "it_software": {
        "name": "软件与 IT",
        "brief": "软件开发、架构、运维。技术名词（API、deploy、merge、commit）保留英文不译。",
        "glossary": "deploy=部署\nmerge=合并\nrollback=回滚",
    },
    "finance": {
        "name": "财务与商务",
        "brief": "财务、成本、采购与商务谈判。数字、币种、比例必须精确，不得口语化改写。",
        "glossary": "EBITDA=息税折旧摊销前利润\ncapex=资本性支出",
    },
    "legal": {
        "name": "法律与合同",
        "brief": "合同条款、合规与法务沟通。用词严谨，保留条款编号与主体全称。",
        "glossary": "",
    },
    "medical": {
        "name": "医疗与器械",
        "brief": "临床、医疗器械与注册事务。术语按医学通用译法，剂量与单位不得改写。",
        "glossary": "",
    },
}


def all_topics(cfg=None) -> dict:
    """可用主题 = 预设 ∪ 自定义 − 已删除；同名 key 时自定义覆盖预设（即预设也可改）。"""
    out = {k: dict(v) for k, v in TOPICS.items()}
    for k in (getattr(cfg, "topics_hidden", None) or []):
        out.pop(k, None)                    # 用户删掉的内置主题不再出现
    custom = getattr(cfg, "topics_custom", None) or {}
    for k, v in custom.items():
        base = dict(out.get(k, {"name": k, "brief": "", "glossary": ""}))
        for kk, vv in (v or {}).items():
            if vv is not None:
                base[kk] = vv
        base["custom"] = True
        out[k] = base
    return out


# 短于此长度的文本不带上下文，减少简短口头语的输入开销。
DEFAULT_CTX_MIN_LEN = 12


# ---------------------------------------------------------------- 语言判定
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
LATIN_RE = re.compile(r"[A-Za-z]{2,}")
ZW_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
PUNCT = str.maketrans({"：": ":", "，": ",", "。": ".", "；": ";", "？": "?", "！": "!",
                       "（": "(", "）": ")", "、": ",", "　": " "})


def normalize_text(text: str) -> str:
    """文本归一化：不改变语义，只消除无意义的表层差异。"""
    t = unicodedata.normalize("NFKC", text or "")
    t = ZW_RE.sub("", t).translate(PUNCT)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def detect_lang(text: str) -> str:
    """脚本级语言判定：零成本、零延迟、结果稳定。

    规则：以 CJK 字数与拉丁词数加权占比判定；一个英文词的信息量约等于
    1.6 个汉字，因此分母用 lat*1.6 归一。阈值 0.25 偏向判为中文——
    中英混说场景里"带英文术语的中文句子"远比"带中文词的英文句子"常见。
    """
    t = ZW_RE.sub("", text or "")
    cjk = len(CJK_RE.findall(t))
    lat = len(LATIN_RE.findall(t))
    if cjk == 0:
        return "en"
    if lat == 0:
        return "zh"
    return "zh" if cjk / (cjk + lat * 1.6) >= 0.25 else "en"


def decide_target(src: str, mode: str):
    """返回目标语言；None 表示无需翻译（已经是目标语言）。"""
    if mode == "zh":
        return None if src == "zh" else "zh"
    if mode == "en":
        return None if src == "en" else "en"
    return "en" if src == "zh" else "zh"        # auto：双向互译


LANG_NAME = {"zh": "中文", "en": "English"}


# ---------------------------------------------------------------- 译文质检
NUM_RE = re.compile(r"[+\-−]?\d[\d,，.]*")
UNIT_ALIASES = {
    'mm': 'mm', 'millimeter': 'mm', 'millimeters': 'mm', '毫米': 'mm',
    'cm': 'cm', 'centimeter': 'cm', 'centimeters': 'cm', '厘米': 'cm',
    'm': 'm', 'meter': 'm', 'meters': 'm', '米': 'm',
    'km': 'km', 'kilometer': 'km', 'kilometers': 'km', '千米': 'km', '公里': 'km',
    'inch': 'in', 'inches': 'in', 'in': 'in', '英寸': 'in',
    'foot': 'ft', 'feet': 'ft', 'ft': 'ft', '英尺': 'ft',
    'kg': 'kg', 'kilogram': 'kg', 'kilograms': 'kg', '千克': 'kg', '公斤': 'kg',
    'g': 'g', 'gram': 'g', 'grams': 'g', '克': 'g',
    'degree': 'deg', 'degrees': 'deg', '°': 'deg', '度': 'deg',
    '%': '%', '％': '%',
}
UNIT_RE = re.compile('|'.join(re.escape(u) + (r'(?![a-z])' if u.isascii() and u.isalpha() else '')
                              for u in sorted(UNIT_ALIASES, key=len, reverse=True)), re.I)


def extract_numbers(text: str) -> set:
    """抽取文本中的数字，归一化千分位与句末点。"""
    out = set()
    for m in NUM_RE.finditer(text or ""):
        raw = m.group().replace(",", "").replace("，", "").replace('−', '-').lstrip('+').rstrip(".")
        if raw.endswith("."):
            raw = raw[:-1]
        if raw and any(c.isdigit() for c in raw):
            out.add(raw)
    return out


def extract_quantities(text):
    """Recognize explicit numeric units, preserving signs and unit scale."""
    quantities = set()
    for number in NUM_RE.finditer(text or ''):
        suffix = (text or '')[number.end():].lstrip()
        unit = UNIT_RE.match(suffix)
        if unit:
            value = next(iter(extract_numbers(number.group())), '')
            quantities.add((value, UNIT_ALIASES[unit.group().lower()]))
    return quantities


def all_glossary_pairs(cfg) -> list:
    """用户术语表 + 当前主题自带术语表，合并为 [(原文, 译文)]。"""
    pairs = []
    for src in (getattr(cfg, "glossary", "") or "",
                (all_topics(cfg).get(getattr(cfg, "topic", ""), {}) or {}).get("glossary", "")):
        for line in (src or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and v:
                    pairs.append((k, v))
    return pairs


def qa_check(source: str, translation: str, cfg, src: str, tgt: str) -> list:
    """返回问题列表 [{type, detail}]。只报可机检的硬问题，不评判文风。"""
    issues = []
    tr = (translation or "").strip()
    if not tr:
        return [{"type": "empty", "detail": "译文为空"}]

    # 1) 数字一致性：原文里的数字必须原样出现在译文里
    sn, tn = extract_numbers(source), extract_numbers(tr)
    missing = sorted(sn - tn)
    if missing:
        issues.append({"type": "number", "detail": "数字未保留：" + "、".join(missing[:5])})
    missing_units = extract_quantities(source) - extract_quantities(tr)
    if missing_units:
        issues.append({"type": "unit", "detail": "数值或单位未保留：" +
                       "、".join(f"{value} {unit}" for value, unit in sorted(missing_units)[:5])})

    # 2) 术语合规：仅在开启强制模式时检查，且只查专有名词（多词或 ≥6 字符）
    pairs = cfg.strict_terms() if hasattr(cfg, "strict_terms") else []
    low_tr = tr.lower()
    for k, v in pairs:
        # 通用单字/短词（trim、gap 这类）语义随上下文变化，硬查会误伤正确译文
        if len(k.split()) < 2 and len(k) < 6:
            continue
        if k.lower() in (source or "").lower() and v.lower() not in low_tr:
            issues.append({"type": "term", "detail": f"术语未遵循：{k} → {v}"})
        elif v.lower() in (source or "").lower() and k.lower() not in low_tr:
            issues.append({"type": "term", "detail": f"术语未遵循：{v} → {k}"})

    # 3) 疑似未翻译：译文与原文高度一致
    if src != tgt and normalize_text(source).lower() == normalize_text(tr).lower():
        issues.append({"type": "untranslated", "detail": "疑似未翻译"})

    # 4) 长度异常：过短像漏译，过长像模型加了解释
    ls, lt = len(source or ""), len(tr)
    if ls >= 8 and lt < max(2, ls * 0.2):
        issues.append({"type": "short", "detail": f"译文过短（{lt}/{ls}）"})
    if ls >= 8 and lt > ls * 4 + 30:
        issues.append({"type": "long", "detail": f"译文过长（{lt}/{ls}）"})

    return issues


def is_translatable(text: str) -> bool:
    """过滤无信息量的片段，避免浪费调用：纯符号/纯数字/过短。"""
    t = normalize_text(text)
    if len(t) < 2:
        return False
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", t):
        return False
    return True


SENT_END = re.compile(r"[.?!。？！…]\s*$")


# ---------------------------------------------------------------- 思考强度
# 各家表达「想多深」的字段名并不统一，因此这里按「最标准 → 最兼容」排成一列
# 候选；请求时逐个试，被上游拒绝就换下一个，命中的那个记进配置不再重复试。
REASONING_LEVELS = ("auto", "off", "low", "medium", "high")
REASONING_LABELS = {"自动": "auto", "关": "off", "低": "low", "中": "medium", "高": "high"}
REASONING_TITLES = {value: key for key, value in REASONING_LABELS.items()}
# 判定「上游不接受这个参数」的错误特征；命中才降级重试，其余错误照旧上抛
_REJECT_HINTS = ("reasoning_effort", "reasoning", "thinking", "unknown", "unsupported",
                 "not support", "unrecognized", "invalid_request", "extra fields",
                 "additional properties", "not allowed")


def reasoning_candidates(level: str):
    """把档位翻译成候选请求字段：[(字段名, {payload 片段})]，无候选表示不改请求。"""
    level = (level or "auto").lower()
    if level not in ("off", "low", "medium", "high"):
        return []
    enabled = level != "off"
    effort = "minimal" if level == "off" else level
    budget = {"off": 0, "low": 1024, "medium": 2048, "high": 4096}.get(level, 2048)
    out = [("reasoning_effort", {"reasoning_effort": effort})]
    if enabled:
        out.append(("thinking", {"thinking": {"type": "enabled"}}))
        out.append(("thinking_budget", {"thinking": {"type": "enabled", "budget_tokens": budget}}))
    else:
        out.append(("thinking", {"thinking": {"type": "disabled"}}))
    out.append(("enable_thinking", {"enable_thinking": enabled}))
    out.append(("chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": enabled}}))
    return out


def rejected_by_param(error: BaseException, field: str) -> bool:
    """上游是否因为「不认识这个字段」而拒绝；用于决定要不要换下一个候选。"""
    text = str(error or "").lower()
    if not text.startswith("http 400") and not text.startswith("http 422"):
        return False
    return any(hint in text for hint in _REJECT_HINTS) or field.lower() in text


def is_sentence_end(text: str) -> bool:
    """句末判定：ASR 已给出终止标点，说明这句话在语法上闭合。

    作为"提前翻译"的信号——有标点且短时间无改写即可提交，
    不必等满 settle_ms；ASR 常吞标点，所以它只是加速通道，不是必要条件。
    """
    t = (text or "").strip()
    return bool(t) and bool(SENT_END.search(t))


def take_context(context, n: int, max_chars: int = 0) -> tuple:
    """取最近 n 句上文（旧→新）；总字符超限时只保留更近的几句。

    句数是个粗粒度旋钮：口语字幕一句 40–80 字，10 句 ≈ 600 字；
    遇到长句连发时按字符收敛，避免提示词被上文撑大拖慢响应。
    """
    if not context or n <= 0:
        return ()
    picked, used = [], 0
    for s in reversed(list(context)[-n:]):
        ln = len(s or "")
        if picked and max_chars and used + ln > max_chars:
            break
        picked.append(s)
        used += ln
    picked.reverse()
    return tuple(picked)


# ---------------------------------------------------------------- 配置
@dataclass
class Config:
    enabled: bool = False                   # 翻译总开关（设置页「翻译」）：
                                            # 关闭时仅记录原文，其余设置照常保存使用
    engine: str = "openai"                  # openai | deepl | azure
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key: str = ""
    azure_region: str = ""
    mode: str = "auto"                      # auto | zh | en
    reasoning: str = "auto"                 # 思考强度：auto | off | low | medium | high
    reasoning_field: str = ""               # 上游实际接受的字段名（探测后记住）
    reading_size: int = 12
    reading_density: str = "comfort"
    theme: str = "light"                   # light | dark（界面外观）
    float_bar: bool = False                 # 悬浮字幕窗开启
    float_on_min: bool = False              # 主窗口最小化时自动切悬浮窗
    float_prev: int = 1                     # 悬浮窗上方保留几条上一句（0 = 只看当前句）
    float_w: int = 460                      # 悬浮条宽度（可拖拽，记住上次尺寸）
    float_h: int = 0                        # 悬浮条高度；0 = 按内容自适应
    meeting_mode: str = "teams"             # 会议模式 key（见 modes.py）
    # ---- 语境与触发 ----
    topic: str = "general"                  # 当前主题 key
    topic_custom: str = ""                  # 临时补充说明，拼在主题 brief 之后
    topics_custom: dict = field(default_factory=dict)   # 用户自定义/覆盖的主题
    topics_hidden: list = field(default_factory=list)   # 已删除的内置主题 key（可恢复）
    context_n: int = 10                     # 附带的上文句数
    ctx_min_len: int = DEFAULT_CTX_MIN_LEN  # 短于此长度不带上文
    ctx_max_chars: int = 900                # 上文总字符上限（长句时按此收敛）
    sentence_end_grace_ms: int = 450        # 句末标点 + 该时长无变化 → 立即翻译
    supersede_grace_ms: int = 250           # 已被新句顶掉 → 该时长后立即翻译
    settle_ms: int = 1200                   # 兜底：无标点且未被顶掉时的等待上限
    merge_gap_ms: int = 1500                # 碎句合并窗口：同人 + 上一条未收句，超过则不折（0 = 关闭）
    concurrency: int = 3                    # 并发在途请求数（吞吐 ≈ 并发/单请求耗时）
    backlog_max: int = 12                   # 启动时最多自动补译多少条历史字幕
    batch_size: int = 4                     # 单请求最多合并几条
    batch_chars: int = 2400                 # 单请求最多字符数
    batch_window_ms: int = 260              # 合并窗口
    min_interval_ms: int = 120              # 两次 API 调用最小间隔（限速）
    timeout_s: int = 15
    max_retry: int = 2
    glossary: str = ""                      # 术语表，每行 "原文=译文"
    glossary_mode: str = "off"              # 兼容旧配置：on 扩大术语质检范围；不控制提示词注入
    fallback_engine: str = ""               # 备用引擎
    fallback_base_url: str = ""
    fallback_model: str = ""
    fallback_api_key: str = ""
    engine_profiles: dict = field(default_factory=dict)
    active_engine_profile: str = ""
    export_preferences: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                known = {f for f in cls.__dataclass_fields__}
                clean = {}
                for k, v in data.items():
                    if k not in known:
                        continue
                    if k == "engine_profiles":
                        clean[k] = {
                            name: {**profile, "api_key": _unprotect(profile.get("api_key", ""))}
                            for name, profile in v.items()
                            if isinstance(name, str) and isinstance(profile, dict)
                        } if isinstance(v, dict) else {}
                    else:
                        clean[k] = _unprotect(v) if k in SECRET_FIELDS else v
                return cls(**clean)
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        # 密钥落盘前加密：配置文件是可读的 JSON，明文密钥等于把账号借给任何
        # 能读到这个文件的程序（同步盘、备份、误发的日志都算）。
        for key in SECRET_FIELDS:
            if data.get(key):
                data[key] = _protect(data[key])
        for profile in data["engine_profiles"].values():
            if isinstance(profile, dict) and profile.get("api_key"):
                profile["api_key"] = _protect(profile["api_key"])
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def topic_instruction(self) -> str:
        """只给领域语境，不塞术语表——术语交给模型按主题常识处理更自然。

        兜底链：当前主题 → general → 任意可用主题 → 空。
        用户可能删掉 general 甚至全部内置主题，此处不能 KeyError。
        """
        topics = all_topics(self)
        t = topics.get(self.topic) or topics.get("general")
        if t is None and topics:
            t = next(iter(topics.values()))
        s = (t or {}).get("brief") or ""
        if self.topic_custom.strip():
            s += "\n补充要求：" + self.topic_custom.strip()
        return s

    def glossary_pairs(self) -> list:
        """术语对照 = 主题自带术语（用户可在主题页里编辑）。

        历史上这里还并过一份「全局术语表」，靠 glossary_mode 开关控制是否
        当硬约束。后来发现两处都写同一个词时无法仲裁，且全局表容易和主题
        说明互相矛盾，于是把术语收归主题所有：换主题就是换整套译名。
        配置里的 glossary 字段仍被读取（旧配置不至于失效），但界面不再写它。
        """
        pairs = []
        for src in (getattr(self, "glossary", "") or "",
                    (all_topics(self).get(self.topic, {}) or {}).get("glossary", "")):
            for line in (src or "").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k and v:
                        pairs.append((k, v))
        return pairs

    def strict_terms(self) -> list:
        """质检硬查用的术语：默认只查**用户自己写的**那部分。

        为什么不是全部：预设主题里的术语是建议，不是验收标准。实测把
        trim=内饰件 当硬约束时，模型正确译成「门板」反而被判「术语未遵循」
        —— 术语表自己成了错误来源。
        而用户亲手敲下的译名就是他想要的叫法，值得查。

        glossary_mode 显式设成 on 时仍然全查（旧配置/脚本的用法）。
        """
        if getattr(self, "glossary_mode", "off") == "on":
            return self.glossary_pairs()
        own = ((getattr(self, "topics_custom", None) or {})
               .get(self.topic) or {}).get("glossary") or ""
        pairs = []
        for line in own.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and v:
                    pairs.append((k, v))
        return pairs

    def topic_version(self) -> str:
        t = all_topics(self).get(self.topic, {})
        raw = "|".join([self.topic, t.get("brief", "") or "", t.get("glossary", "") or "",
                        self.topic_custom.strip()])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

    def engine_id(self) -> str:
        return f"{self.engine}:{self.model or self.base_url}"

    def context_enabled(self, text: str) -> bool:
        return self.context_n > 0 and len(normalize_text(text)) >= self.ctx_min_len



# ---------------------------------------------------------------- 服务端用量
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_hit_tokens", "cache_miss_tokens")


def parse_token_usage(usage):
    """Normalize reported counters; derive a missing cache split only from reported input."""
    usage = usage if isinstance(usage, dict) else {}
    def count(value):
        return value if type(value) is int and value >= 0 else None
    prompt = count(usage.get("prompt_tokens"))
    output = count(usage.get("completion_tokens"))
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    hit = count(usage.get("prompt_cache_hit_tokens"))
    if hit is None:
        hit = count(details.get("cached_tokens"))
    miss = count(usage.get("prompt_cache_miss_tokens"))
    if prompt is not None:
        if ((hit is not None and hit > prompt) or (miss is not None and miss > prompt)
                or (hit is not None and miss is not None and hit + miss != prompt)):
            hit = miss = None
        elif hit is not None and miss is None:
            miss = prompt - hit
        elif miss is not None and hit is None:
            hit = prompt - miss
    return dict(input_tokens=prompt, output_tokens=output,
                cache_hit_tokens=hit, cache_miss_tokens=miss)


def request_key(cfg, text, src, tgt, context):
    """Identity for concurrent requests only; completed translations are never reused."""
    parts = [cfg.engine_id(), cfg.base_url, src, tgt, cfg.topic_instruction(),
             cfg.glossary_pairs(), text, context]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 异常
class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


# ---------------------------------------------------------------- 引擎
class Provider:
    name = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.usage = None

    def translate(self, texts, src, tgt) -> list[str]:
        raise NotImplementedError

    # -- 通用 HTTP ---------------------------------------------------
    def _post(self, url, payload, headers, timeout):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code in (401, 403, 400, 404, 422):
                raise PermanentError(f"HTTP {e.code} {detail}") from e
            raise TransientError(f"HTTP {e.code} {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TransientError(str(e)) from e


class OpenAICompatProvider(Provider):
    """OpenAI /chat/completions 协议，覆盖 DeepSeek、Qwen、Kimi、GLM、OpenAI、Azure OpenAI 等。"""
    name = "openai"

    def _sys_prompt(self, tgt):
        # 提示词按「准确优先、其次省 token」来写：
        #   - 硬规则逐条列清（数字/专名/JSON 形状），模型不用猜；
        #   - 每条都说明「为什么」，遵守率明显高于干巴巴的命令；
        #   - 不复述同一要求（旧版「不要翻译/不要复述/不要写名字」是同义三连）。
        #     系统提示词每次请求都要重发，冗余直接等于 token 成本和首字延迟。
        base = (
            "你是专业的会议同声传译引擎，为实时会议做中英互译。\n"
            f"把每条 item 的 text 译成{LANG_NAME[tgt]}。\n"
            "\n## 硬规则（违反即视为失败）\n"
            "1. 数字、单位、型号、代号、缩写一律照抄，不换算、不改写"
            "（它们往往是唯一的验收依据）；\n"
            "2. 人名、公司名、产品型号保留原文；\n"
            "3. 已是目标语言的条目原样返回，不要顺手润色；\n"
            "4. 不增不减：不补解释、不加评论、不擅自合并条目；\n"
            '5. 只输出 JSON：{"translations": ["…"]}，条数与顺序必须与 text 一致，'
            "不要任何额外文字。\n"
            "\n## 译法\n"
            "口语化、简洁，保留说话人的语气——这是会上有人边听边看的字幕，"
            "宁可短，不要绕。"
        )
        topic = self.cfg.topic_instruction()
        if topic:
            base += (
                "\n\n## 会议背景\n"
                "本场会议的领域与惯用说法。只用于消除歧义（同一个词在别的领域"
                "可能是另一个意思），不要把这里的内容写进译文：\n" + topic
            )
        pairs = self.cfg.glossary_pairs()
        if pairs:
            # 术语表是「统一叫法」，不是「逐词替换」。若写成必须严格遵守，
            # 通用词的正常译法会被一起钉死（实测 trim→饰件 误伤门板）。
            body = "\n".join(f"{k}={v}" for k, v in pairs)
            base += (
                "\n\n## 译名对照\n"
                "原文出现这些词时优先采用对应译名，保证同一场会前后一致；"
                "若它们在当前句明显是别的意思，按上下文译，不要硬套：\n" + body
            )
        # ctx 说明只在真的会带上下文时出现（context_n=0 时纯属浪费 token）
        ctx_n = max(0, int(getattr(self.cfg, "context_n", 0) or 0))
        if ctx_n > 0:
            base += (
                "\n\n## 上文\n"
                f"每条 item 可能带 ctx：最多 {ctx_n} 句上文，由旧到新，"
                "格式「说话人: 内容」，越靠后越贴近当前句。仅用于理解指代、"
                "省略和术语；不要翻译它、不要复述、不要把说话人写进译文。"
            )
        return base


    def translate(self, texts, src, tgt, contexts=None):
        base = (self.cfg.base_url or "").rstrip("/")
        if not base:
            raise PermanentError("未配置 Base URL")
        if not base.endswith("/chat/completions"):
            base += "/chat/completions"
        items = []
        for i, t in enumerate(texts):
            ctx = contexts[i] if contexts and i < len(contexts) else None
            item = {"text": t}
            if ctx:
                item["ctx"] = list(ctx)
            items.append(item)
        payload = {
            "model": self.cfg.model,
            "temperature": 0,
            "stream": False,
            "messages": [
                {"role": "system", "content": self._sys_prompt(tgt)},
                {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
            ],
        }
        self.usage = None
        data, last_field = self._post_with_reasoning(
            base, payload, {"Authorization": f"Bearer {self.cfg.api_key}"},
            self.cfg.timeout_s)
        # 记在内存里即可：下次请求直接命中，不必再失败一遍；持久化交给设置里的探测
        self.cfg.reasoning_field = last_field
        self.usage = parse_token_usage(data.get("usage")) if isinstance(data, dict) else None
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            raise TransientError(f"响应结构异常: {str(data)[:200]}") from e
        return self._parse(content, len(texts))

    def _post_with_reasoning(self, url, payload, headers, timeout):
        """按配置的思考强度发请求；上游不认这个字段就换下一个候选。

        思考强度是纯可选项：没有配置时请求与原版完全一致，不会引入额外调用。
        """
        candidates = reasoning_candidates(getattr(self.cfg, "reasoning", "auto"))
        if not candidates:
            return self._post(url, payload, headers, timeout), ""
        known = (getattr(self.cfg, "reasoning_field", "") or "").strip()
        ordered = sorted(candidates, key=lambda item: 0 if item[0] == known else 1)
        last = None
        for name, extra in ordered:
            try:
                return self._post(url, {**payload, **extra}, headers, timeout), name
            except PermanentError as exc:
                last = exc
                if not rejected_by_param(exc, name):
                    raise
        raise last

    @staticmethod
    def _parse(content: str, n: int) -> list[str]:
        s = (content or "").strip()
        s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
        try:
            obj = json.loads(s)
            arr = obj["translations"] if isinstance(obj, dict) else obj
            arr = [str(x) for x in arr]
            if len(arr) == n:
                return arr
            if len(arr) == 1 and n == 1:
                return arr
            raise ValueError("条数不符")
        except Exception as e:
            # 退化：整段原文只有一条时，直接当译文用
            if n == 1 and s and not s.startswith("{"):
                return [s]
            raise TransientError(f"译文解析失败: {e} / {s[:160]}") from e


class DeepLProvider(Provider):
    name = "deepl"

    def translate(self, texts, src, tgt, contexts=None):
        # 当前适配器只发送原文和语言，未接入会议背景或译名对照。
        url = self.cfg.base_url or "https://api-free.deepl.com/v2/translate"
        if "/v2/translate" not in url:
            url = url.rstrip("/") + "/v2/translate"
        payload = {"text": texts,
                   "target_lang": "ZH" if tgt == "zh" else "EN",
                   "source_lang": "ZH" if src == "zh" else "EN"}
        data = self._post(url, payload,
                          {"Authorization": f"DeepL-Auth-Key {self.cfg.api_key}"},
                          self.cfg.timeout_s)
        try:
            return [t["text"] for t in data["translations"]]
        except Exception as e:
            raise TransientError(f"响应结构异常: {str(data)[:200]}") from e


class AzureProvider(Provider):
    name = "azure"

    def translate(self, texts, src, tgt, contexts=None):
        base = (self.cfg.base_url or "").rstrip("/")
        if not base:
            raise PermanentError("未配置 Azure 端点")
        m = {"zh": "zh-Hans", "en": "en"}
        url = (f"{base}/translate?api-version=3.0&from={m[src]}&to={m[tgt]}")
        headers = {"Ocp-Apim-Subscription-Key": self.cfg.api_key}
        if self.cfg.azure_region:
            headers["Ocp-Apim-Subscription-Region"] = self.cfg.azure_region
        data = self._post(url, [{"Text": t} for t in texts], headers, self.cfg.timeout_s)
        try:
            return [d["translations"][0]["text"] for d in data]
        except Exception as e:
            raise TransientError(f"响应结构异常: {str(data)[:200]}") from e


PROVIDERS = {"openai": OpenAICompatProvider, "deepl": DeepLProvider, "azure": AzureProvider}


def build_provider(cfg: Config, fallback=False) -> Provider:
    if not fallback:
        cls = PROVIDERS.get(cfg.engine, OpenAICompatProvider)
        return cls(cfg)
    # 备用通道：复制配置后覆盖连接参数
    sub = Config(**asdict(cfg))
    sub.engine = cfg.fallback_engine
    sub.base_url = cfg.fallback_base_url
    sub.model = cfg.fallback_model
    sub.api_key = cfg.fallback_api_key
    cls = PROVIDERS.get(sub.engine, OpenAICompatProvider)
    return cls(sub)


def probe_reasoning(cfg: Config, level: str = "", timeout: float = 0) -> tuple:
    """探测上游认哪个思考字段：返回 (字段名, 人话说明)。

    发一条极短请求，逐个尝试候选字段；被「不认识这个参数」拒绝就换下一个。
    命中后写回 ``cfg.reasoning_field`` 并落盘，后续请求不必再失败重试。
    """
    if getattr(cfg, "engine", "openai") != "openai":
        return "", "当前引擎不支持思考参数（只有 OpenAI 兼容引擎有）"
    base = (cfg.base_url or "").rstrip("/")
    if not base:
        return "", "未配置 Base URL"
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    level = (level or getattr(cfg, "reasoning", "auto") or "auto").lower()
    if level not in ("off", "low", "medium", "high"):
        level = "medium"                      # 自动档用中档探测，能区分「支持/不支持」
    if not cfg.model:
        return "", "未配置模型"
    provider = OpenAICompatProvider(cfg)
    payload = {
        "model": cfg.model,
        "temperature": 0,
        "stream": False,
        "messages": [{"role": "user", "content": '只回复两个字：好的'}],
    }
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    for name, extra in reasoning_candidates(level):
        try:
            provider._post(url, {**payload, **extra}, headers, timeout or cfg.timeout_s)
        except PermanentError as exc:
            if rejected_by_param(exc, name):
                continue
            return "", f"请求被拒绝：{exc}"
        except TransientError as exc:
            return "", f"网络或服务异常：{exc}"
        cfg.reasoning_field = name
        try:
            cfg.save()
        except Exception:
            pass
        title = REASONING_TITLES.get(level, level)
        return name, f"上游接受 {name}（{title}档可用）"
    return "", "上游不接受任何思考参数，会按模型默认行为出字"


# ---------------------------------------------------------------- 调度
@dataclass
class Job:
    rid: int
    text: str
    src: str
    tgt: str
    key: str
    ctx: tuple = ()
    enqueued: float = field(default_factory=time.time)
    single: bool = False


class TranslationService:
    """工作线程池 + 合并窗口 + 方向分组 + 单飞去重 + 失败降级。

    吞吐瓶颈不在 CPU 而在服务端推理耗时（V3.2 单请求 2–5s）。若只有 1 个在途
    请求，会议每秒 1 句的产出根本追不上；因此用 N 个 worker 并行在途，
    并对"同一句话正在翻译"做 single-flight 去重，避免并发重复请求同一文本。

    回调签名：
      on_result(rid, translated, meta)   meta: status/error/src/tgt/engine
    """

    def __init__(self, cfg: Config, on_result):
        self.cfg = cfg
        self.on_result = on_result
        self.q: queue.Queue[Job] = queue.Queue()
        self.stop_evt = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.token_totals = dict.fromkeys(TOKEN_FIELDS, 0)
        self.token_reports = dict.fromkeys(TOKEN_FIELDS, 0)
        self.request_sequence = 0
        self.calls = 0            # 实际 API 调用次数
        self.inflight = 0
        self.waiting: dict[str, list[int]] = {}   # key -> 等待同一译文的 rid 列表
        self.threads: list[threading.Thread] = []
        self.last_call = 0.0
        self.dead_until = 0.0     # 熔断：连续永久错误后暂停
        self.last_error = ""

    # -- 生命周期 ----------------------------------------------------
    def start(self):
        if self.threads and any(t.is_alive() for t in self.threads):
            return
        self.stop_evt.clear()
        n = max(1, int(self.cfg.concurrency))
        self.threads = [threading.Thread(target=self._loop, name=f"tr-{i}", daemon=True)
                        for i in range(n)]
        for t in self.threads:
            t.start()

    def stop(self):
        self.stop_evt.set()
        for _ in range(max(1, len(self.threads))):
            try:
                self.q.put_nowait(None)
            except Exception:
                pass

    # -- 提交 --------------------------------------------------------
    def submit(self, rid: int, text: str, context=None, force: bool = False):
        """入口：判定 → 直通判定 → 合并相同在途请求 → 入队。

        context: 该句之前的若干句原文（旧→新），仅用于理解语境，不参与翻译。
        """
        if not is_translatable(text):
            self.on_result(rid, "", {"status": "skip", "reason": "无有效文本"})
            return
        src = detect_lang(text)
        tgt = decide_target(src, self.cfg.mode)
        if tgt is None:
            self.on_result(rid, "", {"status": "skip", "reason": "已是目标语言",
                                     "src": src, "tgt": src})
            return
        ctx = take_context(context, self.cfg.context_n,
                           getattr(self.cfg, "ctx_max_chars", 0))
        if not self.cfg.context_enabled(text):
            ctx = ()                      # 短句不附带上文
        key = request_key(self.cfg, text, src, tgt, ctx)
        with self.lock:
            if not force and key in self.waiting:
                # 同一句已在途中：挂到该批次上，等结果一起回填，不重复请求
                self.waiting[key].append(rid)
                return
            if force:
                self.request_sequence += 1
                key += f":force:{self.request_sequence}"
            self.waiting[key] = [rid]
        self.q.put(Job(rid=rid, text=text, src=src, tgt=tgt, key=key, ctx=ctx))

    # -- 主循环 ------------------------------------------------------
    def _loop(self):
        while not self.stop_evt.is_set():
            try:
                first = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if first is None:
                break
            batch = [first]
            # 队列积压时延长合并窗口，让一个请求装更多条（吞吐优先）
            window = self.cfg.batch_window_ms / 1000.0
            if self.q.qsize() > 6:
                window = max(window, 0.6)
            deadline = time.time() + window
            while len(batch) < self.cfg.batch_size:
                remain = deadline - time.time()
                if remain <= 0:
                    break
                try:
                    nxt = self.q.get(timeout=remain)
                except queue.Empty:
                    break
                if nxt is None:
                    self.stop_evt.set()
                    break
                batch.append(nxt)
            groups: dict[tuple, list[Job]] = {}
            for job in batch:
                groups.setdefault((job.src, job.tgt), []).append(job)
            for (src, tgt), jobs in groups.items():
                self._run_group(jobs, src, tgt)

    def _throttle(self):
        gap = self.cfg.min_interval_ms / 1000.0
        with self.lock:
            wait = self.last_call + gap - time.time()
            self.last_call = max(time.time(), self.last_call + gap)
        if wait > 0:
            time.sleep(wait)

    def _run_group(self, jobs: list[Job], src: str, tgt: str):
        # 按字符预算切块，避免单请求过大
        chunks, cur, size = [], [], 0
        for j in jobs:
            L = len(j.text)
            if cur and size + L > self.cfg.batch_chars:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(j)
            size += L
        if cur:
            chunks.append(cur)
        for chunk in chunks:
            self._run_chunk(chunk, src, tgt)

    def _run_chunk(self, jobs: list[Job], src: str, tgt: str):
        texts = [j.text for j in jobs]
        contexts = [j.ctx for j in jobs]
        attempt = 0
        while True:
            if time.time() < self.dead_until:
                self._fail(jobs, f"服务熔断中：{self.last_error}")
                return
            try:
                self._throttle()
                provider = build_provider(self.cfg)
                with self.lock:
                    self.calls += 1
                try:
                    out = provider.translate(texts, src, tgt, contexts)
                finally:
                    self._record_usage(provider)
                if len(out) != len(texts):
                    raise TransientError(f"返回条数不符 {len(out)}/{len(texts)}")
                for job, tr in zip(jobs, out):
                    tr = (tr or "").strip()
                    with self.lock:
                        waiters = self.waiting.pop(job.key, [job.rid])
                    for rid in waiters or [job.rid]:
                        self.on_result(rid, tr, {"status": "ok",
                                                 "src": src, "tgt": tgt,
                                                 "key": job.key})
                return
            except PermanentError as e:
                # 配置错误：重试无意义，熔断 60s 让用户去改配置
                self.last_error = str(e)[:160]
                self.dead_until = time.time() + 60
                self._fail(jobs, self.last_error)
                return
            except Exception as e:
                attempt += 1
                if attempt > self.cfg.max_retry:
                    # 主通道失败 → 备用通道
                    if self.cfg.fallback_engine and self.cfg.fallback_api_key:
                        if self._try_fallback(jobs, src, tgt):
                            return
                    if len(jobs) > 1 and not jobs[0].single:
                        # 批量失败时拆成单条再试一次，避免整批连坐
                        for j in jobs:
                            self.q.put(Job(rid=j.rid, text=j.text, src=j.src, tgt=j.tgt,
                                           key=j.key, ctx=j.ctx, single=True))
                        return
                    self.last_error = str(e)[:160]
                    self._fail(jobs, self.last_error)
                    return
                time.sleep((0.5 * (2 ** (attempt - 1))) + random.uniform(0, 0.3))

    def _try_fallback(self, jobs, src, tgt):
        try:
            self._throttle()
            provider = build_provider(self.cfg, fallback=True)
            with self.lock:
                self.calls += 1
            try:
                out = provider.translate([j.text for j in jobs], src, tgt, [j.ctx for j in jobs])
            finally:
                self._record_usage(provider)
            if len(out) != len(jobs):
                raise TransientError(f"备用通道返回条数不符 {len(out)}/{len(jobs)}")
            for job, tr in zip(jobs, out):
                tr = (tr or "").strip()
                with self.lock:
                    waiters = self.waiting.pop(job.key, [job.rid])
                for rid in waiters or [job.rid]:
                    self.on_result(rid, tr, {"status": "ok",
                                             "src": src, "tgt": tgt,
                                             "key": job.key,
                                             "engine": "fallback"})
            return True
        except Exception as e:
            self.last_error = f"备用通道失败: {str(e)[:120]}"
            return False

    def _fail(self, jobs, err):
        for j in jobs:
            with self.lock:
                waiters = self.waiting.pop(j.key, [j.rid])
            for rid in waiters or [j.rid]:
                self.on_result(rid, "", {"status": "fail", "error": err,
                                         "src": j.src, "tgt": j.tgt})

    # -- 状态 --------------------------------------------------------
    def _record_usage(self, provider):
        usage = getattr(provider, "usage", None)
        if not isinstance(usage, dict):
            return
        with self.lock:
            for name in TOKEN_FIELDS:
                value = usage.get(name)
                if type(value) is int and value >= 0:
                    self.token_totals[name] += value
                    self.token_reports[name] += 1

    def stats(self):
        with self.lock:
            stats = {name: self.token_totals[name] if self.token_reports[name] else None
                     for name in TOKEN_FIELDS}
            stats.update(calls=self.calls, token_reports=dict(self.token_reports),
                         queue=self.q.qsize(), error=self.last_error)
        return stats

    def test(self):
        """连通性自检：发送一句测试文本，不计入实时服务用量。"""
        t0 = time.time()
        try:
            src = "en"
            tgt = "zh"
            provider = build_provider(self.cfg)
            out = provider.translate(["This is a connectivity test for the translation engine."],
                                     src, tgt)
            return True, f"{provider.name} → {out[0][:60]}  ({time.time()-t0:.2f}s)"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
