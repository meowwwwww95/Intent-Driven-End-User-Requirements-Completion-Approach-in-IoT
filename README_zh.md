# 意图驱动的物联网终端用户需求补全方法（Intent-Driven End-User Requirements Completion Approach in IoT）

[English Version](README.md)

面向智能家居场景的多 Agent 研究原型：输入用户的 TAP 规则（Trigger-Action-Program，`IF ... THEN ...` 形式）与环境设备画像（`env_profile`），通过大模型驱动的多个 Agent 协同，推导出一棵**需求双向追溯树（BRT）**（层级为 `ROOT → INT_TYPE → INT → IRP → DSL`），将用户意图层层精化/抽象为可执行的系统规则（DSL 即 TAP 规则），并进行质量检查（规则冲突检测、满足性检查等）。

## 核心组件

- `Agents/IntentReader.py`：功能意图推断，TAP → 上下文图 → 原子意图（INT）→ 高层意图
- `Agents/QualityPlanner.py`：质量规划，基于质量模板（`config/quality_template_config.py`）规划非功能需求
- `Agents/RealizationPlanner.py`：实现规划，INT → IRP → TAP(DSL) 的展开与满足性反思/修正
- `Agents/QualityChecker.py`：质量检查，规则冲突检测（内置基于 z3 的 SMT 算法，见 `tool/HomeGuard/`）、满足性检查，支持并行
- `model/`：需求双向追溯树、追溯树总线（发布/订阅）、上下文图、设备画像等核心数据模型
- `tool/`：各阶段转换与检索工具（命名即转换方向，如 `SR2CG`、`AtomINT2IRP`、`IRP2TAP`）
- `evaluator/`：评估工具，包含规则集指标（意图覆盖率、实现多样率、规则补全率、规则幻觉率、规则冲突率）、LLM 规则相似度评判与批量评估
- `webui/`：内置 Web 演示页，实时展示运行过程、各 Agent 状态与追溯树更新
- `dataset/`：评测数据集（L1–L5 五个难度层级，每个 case 含 `case_input.json` 与 `case_ground_truth.json`）

运行时架构为单进程多线程：`main.py` 并行启动 IntentReader 与 QualityPlanner，Agent 内部也使用线程池并行调用 LLM / 冲突检测。全局状态（追溯树总线、设备画像等）为模块级单例，因此**一个进程同时只能运行一个 case**；批量运行通过子进程隔离（见 `batch_run.py`）。

## 环境与安装

- Python 3.10+
- 安装依赖：

```bash
pip install -r requirements.txt
```

主要依赖：`openai`（LLM 调用）、`networkx`（上下文图）、`z3-solver`（规则冲突检测）、`fastapi` + `uvicorn`（Web 演示页）、`openpyxl`（评估结果导出）。

## 模型配置

项目通过 OpenAI-compatible 聊天接口接入基座模型，内置 `deepseek` 预设；其他兼容接口可通过通用环境变量接入。

先复制一份环境变量模板并填写：

```bash
cp .env.example .env
```

使用 DeepSeek 的示例配置：

```env
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_CHAT_MODEL=deepseek-chat
DEEPSEEK_REASONING_MODEL=deepseek-reasoner
```

使用其他 OpenAI-compatible 接口时，可直接用通用覆盖项（优先级高于 provider 专属变量）：

```env
AI_PROVIDER=deepseek
AI_API_KEY=your_api_key_here
AI_BASE_URL=https://your-compatible-endpoint/v1
AI_CHAT_MODEL=your-chat-model
AI_REASONING_MODEL=your-reasoning-model
```

说明：

- `AI_PROVIDER` 用于选择内置 provider 预设
- `AI_API_KEY`、`AI_BASE_URL`、`AI_CHAT_MODEL`、`AI_REASONING_MODEL` 的优先级高于 provider 专属变量
- 针对不同兼容接口，可通过 `AI_SUPPORTS_STREAM_OPTIONS`、`AI_JSON_RESPONSE_FORMAT_MODE` 覆盖 `stream_options` 和 `response_format` 的行为

## 输入格式与数据集

每个 case 为一个 JSON 文件，包含 `user_input`（TAP 规则列表）与 `env_profile`（环境设备画像）：

```json
{
  "user_input": ["IF ... THEN ...", "..."],
  "env_profile": [
    {
      "name": "设备名",
      "type": "设备类型",
      "capabilities": [
        {"name": "能力名", "description": "能力描述", "capabilityType": "trigger 或 action"}
      ]
    }
  ]
}
```

评测数据集位于 `dataset/` 目录，按难度分为 L1–L5 五个层级，每个 case 目录下包含：

- `case_input.json`：模型输入（`user_input` + `env_profile`）
- `case_ground_truth.json`：评估用的参考答案（标准规则与意图）

## 运行

### 单个 case

```bash
python main.py --debug \
  --file dataset/L1/case6/case_input.json \
  --out output/L1/case6/our_tool/case6_res.json
```

参数说明：

- `--file`：输入 case JSON 路径，内容包含 `user_input` 和 `env_profile`
- `--out`：输出追溯树 JSON 路径，同时会自动生成对应的 `*_dsl.json`
- `--debug`：启用调试日志
- `--case-id` / `--difficulty` / `--experiment-run-id`：可选，开销统计（`overhead.jsonl`）用的标识，缺省时从路径推断

抽象设备知识默认从 `env_profile` 中自动总结。日志自动写入 `--out` 所在目录：

```text
output/L1/case6/our_tool/
  case6_res.json      # 追溯树
  case6_res_dsl.json  # 生成的 DSL 规则
  run.log             # 摘要日志
  debug.log           # 详细调试日志
  overhead.jsonl      # 开销埋点（LLM 调用 / Agent 运行 / case 运行三层）
```

### 批量运行

要运行的 case 列表在 `batch_run.py` 顶部的 `RUN_SPEC` 中配置：

```bash
python batch_run.py --input_root dataset --workers 4 --debug   # 指定并行度运行
python batch_run.py --input_root dataset --dry_run             # 只列出将运行的 case，不实际执行
```

批量运行时每个 case 由独立子进程执行，日志仍写入各自输出目录；结束后会合并各 case 的 `overhead.jsonl` 为 `output/overhead_merged.jsonl`。

### Web 演示页

```bash
python -m uvicorn webui.app:app --host 127.0.0.1 --port 8000
```

浏览器打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，可以：

- 自动扫描项目中的 `case_input.json`，选择一个本地 case 并启动运行
- 实时展示 `QualityPlanner`、`IntentReader`、`RealizationPlanner`、`QualityChecker` 的状态
- 实时展示追溯树快照与新增节点、关键事件流与最终统计信息

当前限制：仅支持单 case 实时观察，不支持批量任务同时展示。

### 评估

对某个层级的输出目录批量评估（需要对应的 ground truth）：

```bash
python evaluator/batch_evaluator.py ./output/L5 --cases "6" --workers 8
```

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
