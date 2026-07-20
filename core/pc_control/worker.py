"""
PC 控制 Worker - 在独立虚拟环境中执行实际的 Windows 操作
调用方式：python worker.py "任务描述"
"""
import sys
import json
from windows_use import Agent, Browser
from windows_use.providers.ollama import ChatOllama

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "缺少任务描述"}))
        sys.exit(1)

    task = sys.argv[1]
    try:
        llm = ChatOllama(model="qwen3.5:9b")
        agent = Agent(llm=llm, browser=Browser.EDGE, use_vision=False, max_steps=50)
        result = agent.invoke(task=task)
        if hasattr(result, 'content'):
            output = result.content
        elif hasattr(result, 'text'):
            output = result.text
        else:
            output = str(result)
        print(json.dumps({"success": True, "result": output}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))

if __name__ == "__main__":
    main()