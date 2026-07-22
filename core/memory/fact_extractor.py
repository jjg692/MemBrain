"""
L4 事实抽取器：从对话中提取用户偏好/事件/承诺
"""
import re
import json
from typing import List, Dict

from core.config import MEMORY_DEBUG

def log_dbg(msg: str):
    if MEMORY_DEBUG:
        print(f"[Arbitrator] {msg}")


FACT_EXTRACTION_PROMPT_TEMPLATE = """
分析以下对话，提取关于用户的**事实信息**（偏好、习惯、重要事件、人际关系）。

【重要规则】
1. 只提取用户**明确表达**的信息，只从【用户说】的内容中提取
2. 不要从【助手说】的内容中提取任何事实，即使助手提到了用户
3. 如果用户没有表达任何事实信息，输出空数组 []
4. 不要推断，只提取明确说出来的内容
5. 每条事实必须是一句完整的话

分类说明：
- "preference": 喜好/偏好（喜欢吃什么、喜欢什么音乐等）
- "event": 事件（用户提到做了什么事、去了哪里）
- "commitment": 承诺/约定（用户答应做什么）
- "relationship": 人际关系（提到家人、朋友等）
- "state": 用户的状态或情绪（如“我今天心情不好”、“我饿了”）
- "ability": 用户的能力或拥有的东西（如“我会弹吉他”、“我养了一只猫”）

【正确示例】
用户说：我喜欢你
助手说：我也喜欢你，我们一起kirakira吧！
→ 输出：[{{"fact": "用户表达了对香澄的喜欢", "category": "relationship"}}]

【错误示例】← 禁止
用户说：我喜欢你
助手说：我也喜欢你，我们一起kirakira吧！
→ 错误输出：[{{"fact": "用户想kirakira", "category": "preference"}}]

【归因原则】
- 判断一条事实属于谁时，只看这句话是谁说的。
- “我喜欢炸薯条”是助手说的，所以是关于助手的事实，不是用户的事实。
- 如果用户只问了问题而没有表达任何个人事实，输出空数组 []。

【情感对象识别】
- 如果用户说“我喜欢你”或“我爱你”，其中的“你”指代当前助手。
- 抽取事实时，应将“你”替换为助手的角色名（如“香澄”），使事实完整。
- 示例：“我喜欢你” → “用户表达了对香澄的喜欢”

【其他情况】
- 如果用户说的内容不属于以上任何分类（preference / event / commitment / relationship），输出空数组 []。
- 例如：“你好”、“今天天气真好” → 不属于任何分类 → 输出 []

【绝对禁止】
1. 只从【用户说】中提取事实，绝对不要从【助手说】中提取任何内容，即使助手的话里提到了用户。
2. 不要将【助手说】的内容改写或重新表述，然后当作【用户说】的事实。
3. 如果【用户说】只是提问（如“你喜欢什么？”“你叫什么？”），而没有表达任何用户自己的偏好、习惯或事件，那么输出空数组 []。
4. 助手给用户起的昵称、助手表达的情感、助手的喜好，都不能算作用户的事实。
5. 即使助手的回复紧挨着用户的话，也不能把助手说的内容归为用户。

【硬约束】
- 抽取的事实必须以“用户”为主语，描述用户的偏好、习惯、事件、承诺或人际关系。
- 不得以“助手”、“香澄”或“提到”为主语。
- 例：用户说“我学过吉他” → 输出“[{{"fact": "用户学过吉他", "category": "ability"}}]”
- 例：助手说“岳麓山是标志性景点” → 输出“[]”（因为用户没有说自己相关的事）

【示例】
用户说：香澄喜欢吃什么？
助手说：我喜欢炸薯条和白米饭！
→ 正确输出：[]
→ 错误输出：[{{"fact": "用户提到了最喜欢的食物炸薯条", "category": "preference"}}]
解释：用户只是提问，没有表达自己的偏好；助手的回答属于助手自己，不是用户的事实。

【用户说】：{user_msg}
【助手说】：{assistant_msg}

【输出要求】
输出一个 JSON 数组，数组中的每个元素是一个对象，每个对象必须有且仅有两个字段：
- "fact": 事实内容
- "category": 分类

正确格式示例：[{{"fact": "用户喜欢喝咖啡", "category": "preference"}}]
注意：每个对象里 fact 和 category 必须在同一个花括号内，不能分开。

只输出 JSON，不要其他内容。
"""


def extract_facts(user_msg: str, assistant_msg: str, tool_adapter) -> List[Dict]:
    """抽取事实，返回事实列表，失败时返回空列表"""
    try:
                # 转义花括号，防止 .format() 误解析为占位符
        safe_user_msg = user_msg[:500].replace("{", "{{").replace("}", "}}")
        safe_assistant_msg = assistant_msg[:500].replace("{", "{{").replace("}", "}}")
        prompt = FACT_EXTRACTION_PROMPT_TEMPLATE.format(
            user_msg=safe_user_msg,
            assistant_msg=safe_assistant_msg
        )
        result = tool_adapter.chat(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"【用户说】：{user_msg}\n【助手说】：{assistant_msg}"}
            ]
        )
        content = result.get("content", "")
        print(f"事实抽取器的原始输出：{result}")
        
        # ========== 尝试多种方式解析 JSON ==========
        facts = _parse_json_facts(content)
        if facts:
            # 安全过滤：只保留 dict 类型且包含 "fact" 键的元素
            valid = []
            for f in facts:
                if isinstance(f, dict):
                    fact_text = f.get("fact")
                    if fact_text:
                        valid.append(f)
                    else:
                        log_dbg(f"跳过无 fact 字段的条目: {f}")
                else:
                    log_dbg(f"跳过非 dict 条目: {type(f)} -> {str(f)[:100]}")
            
            log_dbg(f"抽取到 {len(valid)} 条事实")
            return valid
        
        log_dbg(f"_parse_json_facts 返回空，content={content[:200]!r}")
        return []
    except Exception as e:
        import traceback
        log_dbg(f"抽取失败: {e}")
        log_dbg(f"抽取失败 traceback: {traceback.format_exc()[:500]}")
        return []


def _parse_json_facts(content: str) -> List[Dict]:
    """健壮的 JSON 解析，处理单引号、Markdown 代码块、多余文本等"""
    if not content or not content.strip():
        log_dbg(f"_parse_json_facts: content 为空")
        return []
    
    log_dbg(f"_parse_json_facts: {content} ")
    
    # 1. 提取可能包含 JSON 的文本块
    #    优先 Markdown 代码块，再尝试裸 [...]
    json_str = _extract_json_str(content)
    if json_str is None:
        log_dbg(f"未找到 [...] 结构")
        return _bruteforce_extract_facts(content)
    
    # 2. 尝试直接解析
    for attempt_name, attempt_str in [
        ("标准 JSON", json_str),
        ("单引号替换", json_str.replace("'", '"')),
    ]:
        try:
            result = json.loads(attempt_str)
            log_dbg(f"{attempt_name} 解析成功: {type(result)}")
            return result
        except json.JSONDecodeError as e:
            log_dbg(f"{attempt_name} 解析失败: {e}")
    
    # 3. 尝试用 ast.literal_eval 处理 Python 格式
    try:
        import ast
        result = ast.literal_eval(json_str)
        if isinstance(result, list):
            log_dbg(f"ast.literal_eval 解析成功: {len(result)} 条")
            return result
    except Exception as e:
        log_dbg(f"ast.literal_eval 解析失败: {e}")
    
    # 4. 终极兜底：暴力正则提取
    log_dbg(f"所有结构化解析均失败，暴力提取 fact 字段")
    return _bruteforce_extract_facts(json_str)


def _extract_json_str(content: str) -> str | None:
    """从文本中提取 JSON 字符串"""
    # Markdown 代码块
    code_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
    if code_match:
        return code_match.group(1)
    # 裸 [...]
    json_match = re.search(r'\[.*?\]', content, re.DOTALL)
    if json_match:
        return json_match.group()
    return None


def _bruteforce_extract_facts(text: str) -> List[Dict]:
    """暴力提取：从任意格式文本中提取 "fact": "xxx" 内容"""
    fact_pattern = re.compile(r'"[Ff]act"\s*:\s*"((?:[^"\\]|\\.)*)"')
    category_pattern = re.compile(r'"[Cc]ategory"\s*:\s*"((?:[^"\\]|\\.)*)"')
    
    fact_matches = fact_pattern.findall(text)
    category_matches = category_pattern.findall(text)
    
    if not fact_matches:
        return []
    
    result = []
    for i, fact_text in enumerate(fact_matches):
        category = category_matches[i] if i < len(category_matches) else "general"
        result.append({"fact": fact_text, "category": category})
    
    log_dbg(f"暴力提取: {len(result)} 条事实")
    return result