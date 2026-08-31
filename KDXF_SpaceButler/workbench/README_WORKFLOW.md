# 工作流视图 — Workflow Orchestration & Execution

## 概述

在 SpaceButler 家庭空间智能工作台中新增第 **7** 个视图：**工作流**（Workflow），为用户提供家庭场景工作流的 AI 生成、手动拖拽编排与可视化执行能力。

该视图作为纯前端模块独立于已有 6 个视图（首页、AI 管家、空间设备、主动服务、能源与事件、夜间安全），不修改任何已有代码。

## 功能

### 1. AI 生成工作流
- 用户输入自然语言（例如"我要睡觉了"、"帮我省电"）
- 系统通过关键词匹配选择对应的场景模板（对接后端 `ScenePlanner` 的 5 个场景）
- 自动生成设备步骤列表并填充到编排页，带打字机效果

### 2. 手动拖拽编排
- 通过下拉框选择家庭电器添加到工作流
- 每个步骤可配置：设备、动作、参数、延迟时间（分钟）
- 支持拖拽左侧手柄调整执行顺序
- 支持删除单个步骤

### 3. 已保存工作流管理
- 左侧可折叠侧边栏展示所有已保存工作流
- 点击卡片进入执行页
- 点击"编辑"按钮回到编排页加载该工作流
- 5 个预置场景模板：回家模式、观影模式、睡眠模式、离家模式、节能模式
- 数据通过 `localStorage` 持久化

### 4. 工作流执行页
- 展示当前场景名称和启动口令
- **Agent 推理链路可视化**：8 步渐进展示（槽位提取 → 意图识别 → 环境感知 → 计划生成 → 安全校验 → 工具执行 → 结果验证 → 反思评估）
- 执行时每步时间线卡片高亮（蓝色执行中 → 绿色完成）
- 设备状态面板同步更新（琥珀色 executing → 绿色 done）
- 执行完成后展示 AI 反思评估建议

## 文件结构

```
KDXF_SpaceButler/workbench/
├── index.html          # +120 行：工作流视图 HTML（编排 + 执行 + 已保存侧边栏）
├── styles.css           # +230 行：工作流全部样式（末尾追加）
├── app.js              # +400 行：工作流模块（末尾追加，wf 前缀命名空间）
└── README_WORKFLOW.md # 本文件
```

## 架构设计

### 解耦原则
- 所有新增代码通过 `wf` 前缀隔离（`wfState`、`wfRenderBuilder`、`wfRunWorkflow` 等）
- 不修改任何已有 6 个视图的 HTML/CSS/JS
- 不修改 `switchView()`、`api()`、`loadStatus()` 等已有函数
- 数据存储使用独立 key：`spacebutler.workflows`

### 状态管理
```js
const wfState = {
  workflow:    // 当前编辑的工作流
  saved:       // 已保存的工作流列表
  activeIndex: // 当前选中索引
  draggedIndex: // 拖拽排序跟踪
  running:     // 执行状态锁
  wfTab:       // "builder" | "runner"
  aiGenBusy:   // AI 生成锁
};
```

### 模板数据
```js
const WF_PRESETS = {     // 5 个预置场景
  home, movie, sleep, away, energy
};

const WF_DEVICES = [      // 9 台模拟设备
  客厅窗帘, 客厅空调, 客厅主灯, 热水器, 空气净化器,
  客厅电视, 扫地机器人, 卧室灯, 卧室空调
];
```

## 本地运行

```powershell
cd F:\EdgeHome_Agent
python -m http.server 8010 --directory KDXF_SpaceButler/workbench
```

浏览器访问：**http://127.0.0.1:8010/**

点击左侧导航栏"工作流"按钮进入该视图。

## 验证

```powershell
node --check KDXF_SpaceButler/workbench/app.js
```

## 技术说明

- 本项目为纯静态前端，不依赖后端 API
- 工作流数据通过 `localStorage` 持久化
- SVG 图标使用 [Lucide](https://lucide.dev/) 库
- 后端 Agent 能力（`ScenePlanner`、`SlotExtractor`、`IntentClassifier` 等）已实现在 `F:\EdgeHome_Agent\agent/` 中
- 该前端视图是后端 AI Agent 能力的 UI 展示层

## 参赛信息

本项目为第八届中国研究生人工智能创新大赛参赛作品的一部分，赛题为"生成式大语言模型与智能体"，技术方向为智能家居场景的端侧自主服务智能体。