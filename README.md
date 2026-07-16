# 🧠 MemBrain

> “诶嘿～你还记得我说过什么呀？キラキラ☆ドキドキ！”

一个会记住你的 AI 助手，现在暂时由《BanG Dream!》的户山香澄担任。


## 📋 目录

- [它现在能干啥？](#-它现在能干啥)
- [项目结构](#-项目结构)
- [技术架构](#-技术架构)
- [怎么跑起来](#-怎么跑起来)
- [环境变量配置](#-环境变量配置)
- [用了啥](#-用了啥)
- [为什么叫 MemBrain](#-为什么叫-membrain)


## 🎯 它现在能干啥？

| 功能 | 状态 |
|------|------|
| 💬 跟你聊天，记得你说过啥 | ✅ |
| 👤 多用户切换，各聊各的 | ✅ |
| 🎭 自定义人设（现在是香澄） | ✅ |
| 🌐 联网搜索（百度API，天气/新闻/推荐） | ✅ |
| 🖼️ 看懂你发的图（多模态） | ✅ |
| 🔊 把话说出来（TTS 语音合成） | ✅ |
| 🎪 Live2D 桌面上蹦跶 | 🚧 规划中 |


## 📁 项目结构

```
MemBrain/
├── web_app.py                 # 入口，运行这个就行
├── tts_client.py              # TTS 语音合成
├── role_prompt.txt            # 角色人设
│
├── core/                      # 基础设施
│   ├── config.py              # 配置管理（.env）
│   ├── adapters.py            # LLM 适配器（Ollama / DeepSeek）
│   ├── tools.py               # 搜索工具
│   ├── state.py               # Agent 状态定义
│   ├── logger.py              # 日志
│   └── memory/
│       ├── vector_store.py    # ChromaDB 封装（单例）
│       ├── retriever.py       # 混合检索器
│       ├── memory_manager.py  # 五层记忆管理器
│       └── fact_extractor.py  # 事实抽取器
│
├── agent/                     # Agent 逻辑
│   ├── graph.py               # LangGraph 图
│   ├── router.py              # 路由分类器
│   └── handlers/
│       ├── personal.py        # 闲聊处理
│       ├── realtime.py        # 搜索处理
│       └── result.py          # 结果处理
│
├── templates/chat.html        # 前端
├── chromadb/                  # 用户记忆（自动生成）
├── models/                    # 嵌入模型
├── .env.example
├── requirements.txt
└── README.md
```

## 🧠 技术架构

### 双模型分工

| 模型 | 职责 |
|------|------|
| **主模型** | 最终回复生成（支持多模态） |
| **工具模型** | 路由分类 + 查询改写 + 事实抽取 |

### 路由分流

```
用户消息 → router.py
               ├─ REALTIME  → 联网搜索 → 主模型生成回复
               └─ PERSONAL  → 记忆检索 → 主模型生成回复
                                        └─ 记忆为空 → 主模型自主决策是否搜索
```

### 四层记忆架构

| 层级 | 说明 |
|------|------|
| **L1** | 内存上下文（当前会话，最多20轮，超限自动压缩） |
| **L2** | 短期记忆（ChromaDB，持久化，FIFO淘汰保留50轮） |
| **L4** | 重要事实（从对话中自动抽取偏好/事件/承诺） |
| **L5** | 角色记忆（静态人设 prompt） |

### 检索流程

```
用户查询 → 向量检索（语义）
          → BM25 检索（关键词）
          → Cross-Encoder 精排
          → 事实优先注入 → LLM 生成回复
```


## 🚀 怎么跑起来

# 克隆项目
git clone https://github.com/jjg692/MemBrain.git
cd MemBrain

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env   # 修改 API 密钥和模型

# 启动服务
python web_app.py      # 浏览器打开 http://localhost:8000


## 启动顺序（如果启用 TTS）
# 1. 启动 GPT-SoVITS TTS 服务（在整合包目录下）
cd gpt-sovits
.\runtime\python.exe api.py -a "127.0.0.1" -p 9880 -s "SoVITS_weights/你的模型.pth" -g "GPT_weights/你的模型.ckpt" -dr "参考音频.wav" -dt "参考音频文本" -dl "ja"

# 2. 启动 MemBrain 服务
cd MemBrain
python web_app.py

## 📦 用了啥
Python / FastAPI / LangGraph / ChromaDB / Edge-TTS / Ollama / Qwen / Sentence-Transformers

## 🤔 为什么叫 MemBrain
Memory + Brain，无论怎样要比「AI_Chat_Project_v8_final」好听点。

> “呐呐！一起闪闪发光吧！キラキラ☆ドキドキ！”