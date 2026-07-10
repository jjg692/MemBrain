# 🧠 MemBrain

一个会记住你的 AI 助手。

## 项目结构

```
MemBrain/
├── web_app.py           # 入口，运行这个就行
├── templates/
│   └── chat.html        # 聊天界面，想改样式改这个
├── chromadb/            # 用户记忆存这里，启动后自动生成
├── static/              # 静态文件（目前空的）
├── .env.example         # 配置模板，复制成 .env 用
├── .gitignore           # 不上传的文件清单
└── requirements.txt     # Python 依赖清单
```

## 这玩意能干啥？

- 跟你聊天，记得你说过啥 ✅
- 多用户切换，各聊各的 ✅
- 自定义人设 ✅
- 联网搜索（TODO）
- 看懂你发的图（TODO）
- 把话说出来（TODO）
- Live2D 桌面上蹦跶（TODO）

## 怎么跑起来

```bash
git clone https://github.com/jjg692/MemBrain.git
cd MemBrain
pip install -r requirements.txt
cp .env.example .env   # 改你的 API 地址
python web_app.py      # 浏览器打开 http://localhost:8000
```

## 用了啥

Python / FastAPI / ChromaDB / LangChain / WebSocket / Ollama

## 为什么叫 MemBrain

Memory + Brain，一个缝合怪名字，但比「AI_Chat_Project_v8_final」好听点。