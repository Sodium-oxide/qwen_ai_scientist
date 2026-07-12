# PaperWriter + Reviewer 开发排坑记录

> 角色四（论文写作与评议）的最小可跑环境搭建过程。

## 环境

- Python 3.12
- 阿里云 TokenPlan API（`qwen3.6-plus`），OpenAI 兼容协议
- 本地代理：qwenpaw（`http://127.0.0.1:8088`，本项目不经过代理，直连远程）

## 问题1：API Key 格式与认证（最坑）

### 现象
```
DashScope call failed: status_code=401, code=InvalidApiKey
```

### 根因
项目默认用 DashScope SDK（`dashscope.Generation.call()`）调阿里云 API。但 TokenPlan 套餐的 Key（`sk-sp-D...` 格式）走的是 **OpenAI 兼容协议**，不是 DashScope 原生协议。

DashScope 标准 Key：`sk-xxxxxxxx`（16进制）
TokenPlan Key：     `sk-sp-D.xxxx...`（带 `-sp` 段）

两者 API 协议不同，不能混用。

### 解决方案
修改 `v8/qwen_adapter.py`：
1. 检测 Key 前缀 `sk-sp-D` → TokenPlan 模式
2. TokenPlan 模式使用 `openai.OpenAI()` 客户端，base_url 指向：
   ```
   https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
   ```
3. 标准模式保持原有 `dashscope.Generation.call()`

### 关键代码位置
- `v8/qwen_adapter.py` — `_is_tokenplan_key()` 检测函数
- `v8/qwen_adapter.py` — `_create_via_tokenplan()` OpenAI 兼容路径
- `v8/.env` — API Key 和模型名配置

### 不同开发者的兼容策略
- **标准 DashScope 用户**：不设 `.env`，用环境变量 `QWEN_API_KEY=sk-xxx`
- **TokenPlan 用户（你）**：在 `v8/.env` 中配置自己的 Key，`.env` 已在 `.gitignore`
- **Anthropic 用户**：不设 `QWEN_API_KEY`，自动 fallback 到 Anthropic
- 各人 **不改 `qwen_adapter.py`**，只通过自己的 `.env` 或环境变量区分

---

## 问题2：LLM 返回 JSON 中的 LaTeX 转义

### 现象
```
JSON parse error: Invalid \escape
```
LLM 在 JSON 字符串里直接输出 LaTeX 公式如 `$\theta$`、`$\epsilon$`。

### 根因
`\t`、`\e`、`\D` 等不是合法 JSON 转义序列（JSON 只认 `\" \\ \/ \b \f \n \r \t \uXXXX`）。

### 解决方案
在 `_parse_json()` 失败后，调用 `_repair_json()` → `_escape_latex_in_json()`：
- 遍历 JSON 候选文本
- 在字符串内部（`"..."` 中），把 `\<非转义字符>` 替换为 `\\<字符>`
- 例如 `\theta` → `\\theta`

### 关键代码位置
- `v8/paper_writer.py` — `_escape_latex_in_json()`
- `v8/reviewer.py` — 同函数（两份代码独立维护）

---

## 问题3：LaTeX 模板花括号冲突

### 现象
```
KeyError: 'article'
KeyError: 'inputenc'
```

### 根因
LaTeX 模板中用 `.format()` 做变量替换，但 LaTeX 本身大量使用花括号：
- `\documentclass{article}` → Python 把 `{article}` 当成占位符
- `\usepackage[utf8]{inputenc}` → 同上

### 解决方案
所有 LaTeX 字面花括号改成双花括号：
- `{article}` → `{{article}}`
- `{inputenc}` → `{{inputenc}}`
- 真正的 Python 占位符保持单花括号：`{title}`, `{abstract}`

### 关键代码位置
- `v8/paper_writer.py` — `LATEX_TEMPLATE` 常量

---

## 问题4：`.env` 文件 Key 带换行符

### 现象
Key 长度：配置 115 字符 → 实际读到 117 字符 → 401 认证失败

### 根因
`.env` 文件中 Windows 换行符 `\r\n` 被附加到 Key 值末尾。

### 解决方案
在 `qwen_adapter.py` 中调用 `self.api_key.strip()` 去掉首尾空白。

---

## 当前可跑状态

```powershell
# PaperWriter（生成论文）
cd v8
python paper_writer.py          # 交互模式，回车用 demo
python paper_writer.py xxx.json # 从上下文文件读取

# Reviewer（评审论文）
cd v8
python reviewer.py              # 交互模式，回车用 demo
python reviewer.py paper.txt    # 从文件读取
```

## 输出目录
- `v8/.science/papers/` — 论文 JSON / LaTeX / 纯文本
- `v8/tool_results/` — PaperWriter 和 Reviewer 的工具调用结果

## GitHub 提交建议

### 需要提交的
- `v8/paper_writer.py` — PaperWriter 完整实现
- `v8/reviewer.py` — Reviewer 完整实现
- `v8/qwen_adapter.py` — TokenPlan 兼容修改
- `v8/deepseek_adapter.py` — DeepSeek 备选（如有）

### 不要提交的（.gitignore 已覆盖）
- `v8/.env` — 包含个人 API Key
- `v8/__pycache__/` — Python 缓存
- `v8/.science/papers/` — 生成的论文输出
- `v8/tool_results/` — 工具结果

### 队友兼容性
- 不用 TokenPlan 的队友：不改任何配置，原代码路径不受影响
- TokenPlan 队友：在 `v8/` 下创建自己的 `.env`，设 `QWEN_API_KEY` + `QWEN_MODEL_ID`
- 建议队长在主 `config.py` 里确认 `DEFAULT_LLM_PROVIDER` 的逻辑覆盖所有场景
