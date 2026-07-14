# 🧠 MemBrain

> “诶嘿～你还记得我说过什么呀？キラキラ☆ドキドキ！”

一个会记住你的 AI 助手，现在已经进化成《BanG Dream!》的户山香澄了。


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
| 🖼️ 看懂你发的图（qwen3.5:9b 多模态） | ✅ |
| 🔊 把话说出来（GPT-SoVITS TTS 语音合成） | ✅ |
| 🎪 Live2D 桌面上蹦跶 | 🚧 规划中 |


## 📁 项目结构
MemBrain/
├── web_app.py                      # FastAPI 入口，运行这个就行
├── tts_client.py                   # TTS 语音合成客户端
├── core/                           # 核心基础设施
│   ├── config.py                   # 配置加载（.env）
│   ├── adapters.py                 # LLM 适配器（Ollama/DeepSeek）
│   ├── tools.py                    # 搜索工具定义
│   ├── state.py                    # Agent 状态定义
│   ├── logger.py                   # 统一调试日志
│   └── memory/
│       ├── vector_store.py         # ChromaDB 向量存储
│       └── memory_manager.py       # 记忆存储/摘要/清理
├── agent/                          # Agent 核心
│   ├── graph.py                    # LangGraph 图构建与调度
│   ├── router.py                   # 问题类型路由分类
│   └── handlers/                   # 各类型处理器
│       ├── personal.py             # PERSONAL 分支（记忆）
│       ├── realtime.py             # REALTIME 分支（搜索）
│       ├── hybrid.py               # HYBRID 分支（指代消解+搜索）
│       └── result.py               # 搜索结果处理
├── templates/
│   └── chat.html                   # 聊天界面
├── chromadb/                       # 用户记忆（启动后自动生成）
├── models/                         # 嵌入模型（all-MiniLM-L6-v2）
├── static/                         # 静态文件
├── role_prompt.txt                 # 角色提示词
├── .env.example                    # 配置模板
├── .gitignore                      # 不上传的文件清单
└── requirements.txt                # Python 依赖清单

## 🧠 技术架构

### 整体架构图
用户提问 → web_app.py (WebSocket)
                ↓
        agent/graph.py (LangGraph 调度)
                ↓
        agent/router.py (路由分类)
                ↓
    ┌───────────┼───────────┐
    ↓           ↓           ↓
PERSONAL    REALTIME    HYBRID
(记忆)      (搜索)      (指代消解+搜索)
    ↓           ↓           ↓
    └───────────┼───────────┘
                ↓
        core/memory/ (记忆存储)
        core/adapters/ (LLM调用)
                ↓
            回复用户


### 双模型分工

| 模型 | 职责 | 说明 |
|------|------|------|
| **主模型（9B）** | 最终回复生成 | 支持多模态图片识别，qwen3.5:9b |
| **工具模型（7B）** | 路由分类 + 指代消解 | 负责判断问题类型，qwen2.5:7b |

### 智能路由分流

| 类型 | 触发场景 | 处理方式 |
|------|----------|----------|
| **PERSONAL** | 闲聊、打招呼、个人偏好、习惯 | 走记忆检索，记忆为空时主模型自主决策是否搜索 |
| **REALTIME** | 天气、新闻、推荐等实时信息 | 直接走联网搜索 |
| **HYBRID** | 包含指代词的问题（“那个”、“刚才说的”） | 指代消解 → 主模型自主决策是否搜索 |

### 记忆系统

| 记忆类型 | 存储介质 | 特点 |
|----------|----------|------|
| **短期记忆** | ChromaDB 向量库 | 持久化存储，重启不丢失，FIFO 淘汰（保留最近10轮） |
| **长期记忆** | ChromaDB 向量库 | LLM 生成的对话摘要 + 情绪标签，语义检索 |

### 模块职责

| 模块 | 职责 |
|------|------|
| `core/adapters.py` | 统一 LLM 调用接口，支持 Ollama / DeepSeek |
| `core/memory/vector_store.py` | ChromaDB 增删改查，元数据过滤 |
| `core/memory/memory_manager.py` | 短期记忆存储、长期记忆摘要生成、FIFO 淘汰 |
| `agent/router.py` | 调用工具模型判断问题类型 |
| `agent/handlers/` | 各类型问题的具体处理逻辑 |
| `agent/graph.py` | LangGraph 图构建，节点调度，循环控制 |


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
技术	说明
Python 3.11	主要开发语言
FastAPI	Web 框架
WebSocket	实时双向通信
LangGraph	Agent 编排框架
ChromaDB	向量数据库（记忆系统）
Ollama	本地 LLM 运行时
Qwen	主模型（qwen3.5:9b）+ 工具模型（qwen2.5:7b）
GPT-SoVITS	TTS 语音合成
百度搜索 API	联网搜索
Sentence Transformers	文本嵌入（all-MiniLM-L6-v2）

## 🤔 为什么叫 MemBrain
Memory + Brain，一个缝合怪名字，但比「AI_Chat_Project_v8_final」好听点。

> “呐呐！一起闪闪发光吧！キラキラ☆ドキドキ！”