# ResearchAgent

本地化的大模型 RL 研究助手与知识库系统。

## 1. 创建隔离环境

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r ResearchAgent/requirements.txt
playwright install chromium
```

可选脚本：

```bash
bash ResearchAgent/scripts/create_venv.sh
```

## 2. 配置密钥

本项目不会把密钥写入代码。请在本地使用环境变量：

```bash
export GEMINI_API_KEY="your-gemini-key"
export GITHUB_TOKEN="your-github-token"
```

也可复制 `ResearchAgent/.env.example` 的变量名自行注入。

## 3. 运行一次抓取与生成

```bash
python ResearchAgent/main.py run --limit 5
```

## 4. 启动 Web UI

```bash
python ResearchAgent/main.py serve
```

默认地址：`http://127.0.0.1:8000`

## 5. 每日自动调度

```bash
python ResearchAgent/main.py schedule --run-immediately
```

默认按 `RESEARCH_AGENT_SCHEDULE_TIME`（`HH:MM`）每日执行一次。
