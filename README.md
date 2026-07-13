# 🧠 MemBrain

> “诶嘿～你还记得我说过什么呀？キラキラ☆ドキドキ！”

一个会记住你的 AI 助手，现在已经进化成《BanG Dream!》的户山香澄了。

---

## 📋 目录

- [它现在能干啥？](#-它现在能干啥)
- [项目结构](#-项目结构)
- [技术架构亮点](#-技术架构亮点)
- [怎么跑起来](#-怎么跑起来)
- [环境变量配置](#-环境变量配置)
- [项目历程](#-项目历程)
- [用了啥](#-用了啥)
- [为什么叫 MemBrain](#-为什么叫-membrain)

---

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

---

## 📁 项目结构
MemBrain/
├── web_app.py # 入口，运行这个就行
├── agent_graph.py # LangGraph Agent 核心（路由器 + 记忆/搜索分流）
├── tts_client.py # TTS 语音合成客户端
├── templates/
│ └── chat.html # 聊天界面，想改样式改这个
├── chromadb/ # 用户记忆存这里，启动后自动生成
├── models/ # 嵌入模型（all-MiniLM-L6-v2）
├── static/ # 静态文件
├── role_prompt.txt # 角色提示词（现在是香澄）
├── .env.example # 配置模板，复制成 .env 用
├── .gitignore # 不上传的文件清单
└── requirements.txt # Python 依赖清单


---

## 🧠 技术架构亮点

### 双模型分工

| 模型 | 职责 | 说明 |
|------|------|------|
| **主模型（9B）** | 最终回复生成 | 支持多模态图片识别，qwen3.5:9b |
| **工具模型（7B）** | 判断问题类型和工具调用 | 负责路由分类，qwen2.5:7b |

### 智能路由分流

| 类型 | 触发场景 | 处理方式 |
|------|----------|----------|
| **CHAT** | 闲聊、打招呼、情感表达 | 直接回复，不走记忆/搜索 |
| **PERSONAL** | 个人关系、偏好、习惯 | 走记忆检索 |
| **REALTIME** | 天气、新闻、推荐等实时信息 | 走联网搜索 |
| **HYBRID** | 指代模糊的问题 | 记忆补全 → 搜索 |

### 记忆系统

- **长期记忆**：ChromaDB 向量存储，跨会话持久化
- **短期记忆**：对话历史，当前会话上下文

---

## 🚀 怎么跑起来

```bash
# 克隆项目
git clone https://github.com/jjg692/MemBrain.git
cd MemBrain

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env   # 修改 API 密钥和模型

# 启动服务
python web_app.py      # 浏览器打开 http://localhost:8000


启动顺序（如果启用 TTS）
bash
# 1. 启动 GPT-SoVITS TTS 服务（在整合包目录下）
cd gpt-sovits
.\runtime\python.exe api.py -a "127.0.0.1" -p 9880 -s "SoVITS_weights/你的模型.pth" -g "GPT_weights/你的模型.ckpt" -dr "参考音频.wav" -dt "参考音频文本" -dl "ja"

# 2. 启动小柚服务
cd MemBrain
python web_app.py

⚙️ 环境变量配置
变量	说明	示例
LLM_MODEL	主模型（多模态）	qwen3.5:9b
TOOL_LLM_MODEL	工具模型（路由）	qwen2.5:7b
BAIDU_API_KEY	百度搜索 API 密钥	Bearer xxx...
HOST	服务绑定地址	0.0.0.0
PORT	服务端口	8000

📦 用了啥
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

🤔 为什么叫 MemBrain
Memory + Brain，一个缝合怪名字，但比「AI_Chat_Project_v8_final」好听点。

呐呐！一起闪闪发光吧！キラキラ☆ドキドキ！