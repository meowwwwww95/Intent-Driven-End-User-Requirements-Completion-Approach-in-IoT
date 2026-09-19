const AGENT_ORDER = ["IntentReader", "QualityPlanner", "RealizationPlanner", "QualityChecker"];
const AGENT_DESCRIPTIONS = {
  IntentReader: "TAP 解析、意图提取、意图抽象与可满足性检查",
  QualityPlanner: "质量模板检索、实现逻辑生成、实现满足性检查",
  RealizationPlanner: "意图分解、规划实现、规则生成与实现检查",
  QualityChecker: "INT 满足性检查、IRP/DSL 冲突检查",
};
const MAX_STORED_EVENTS = 2000;

const state = {
  cases: [],
  run: null,
  events: [],
  eventSource: null,
  latestTree: null,
  latestTreeSummary: null,
};

const els = {
  caseSelect: document.getElementById("caseSelect"),
  refreshCasesBtn: document.getElementById("refreshCasesBtn"),
  startRunBtn: document.getElementById("startRunBtn"),
  controlMessage: document.getElementById("controlMessage"),
  runStatus: document.getElementById("runStatus"),
  currentCase: document.getElementById("currentCase"),
  traceTree: document.getElementById("traceTree"),
  agentSwimlane: document.getElementById("agentSwimlane"),
};

function fmtDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function fmtSeconds(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${Number(value).toFixed(2)} s`;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setControlMessage(message, isError = false) {
  els.controlMessage.textContent = message;
  els.controlMessage.style.color = isError ? "var(--danger)" : "var(--muted)";
}

async function loadCases() {
  setControlMessage("正在加载 case 列表...");
  try {
    const response = await fetch("/api/cases");
    const data = await response.json();
    state.cases = Array.isArray(data.cases) ? data.cases : [];
    renderCaseOptions();
    setControlMessage(state.cases.length ? `已加载 ${state.cases.length} 个 case。` : "未发现 case_input.json，请检查项目目录。");
  } catch (error) {
    setControlMessage(`加载 case 列表失败：${error}`, true);
  }
}

function renderCaseOptions() {
  els.caseSelect.innerHTML = "";
  if (!state.cases.length) {
    const option = document.createElement("option");
    option.textContent = "无可用 case";
    option.value = "";
    els.caseSelect.appendChild(option);
    return;
  }
  state.cases.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.path;
    option.textContent = item.label;
    els.caseSelect.appendChild(option);
  });
}

function resetRunView() {
  state.events = [];
  state.latestTree = null;
  state.latestTreeSummary = null;
  render();
}

async function startRun() {
  const casePath = els.caseSelect.value;
  if (!casePath) {
    setControlMessage("请先选择一个 case。", true);
    return;
  }
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  resetRunView();
  setControlMessage("正在启动运行...");
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        case_path: casePath,
        debug: true,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "启动失败");
    }
    state.run = data;
    setControlMessage("运行已启动，正在订阅实时事件。");
    render();
    openEventStream(data.run_id);
  } catch (error) {
    setControlMessage(`启动失败：${error.message || error}`, true);
  }
}

function openEventStream(runId) {
  state.eventSource = new EventSource(`/api/runs/${runId}/events`);
  state.eventSource.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    applyEvent(payload);
  };
  state.eventSource.addEventListener("done", () => {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    setControlMessage("运行结束，实时事件流已关闭。");
  });
  state.eventSource.onerror = () => {
    setControlMessage("事件流连接已断开，若运行已结束可忽略。");
  };
}

function applyEvent(event) {
  state.events.push(event);
  if (state.events.length > MAX_STORED_EVENTS) {
    state.events = state.events.slice(-MAX_STORED_EVENTS);
  }

  if (!state.run) {
    state.run = { run_id: event.run_id, agent_states: {}, status: "running" };
  }

  state.run.event_count = event.seq;

  if (event.event_type === "run.started") {
    state.run.status = "running";
    state.run.started_at = event.timestamp;
  } else if (event.event_type === "run.failed") {
    state.run.status = "failed";
    state.run.ended_at = event.timestamp;
  } else if (event.event_type === "run.finished") {
    state.run.status = state.run.status === "failed" ? "failed" : "finished";
    state.run.ended_at = event.timestamp;
    state.run.elapsed_seconds = event.payload.elapsed_seconds;
    state.run.total_tokens = event.payload.total_tokens;
    state.run.summary_lines = event.payload.summary_lines || [];
  }

  if (event.agent) {
    state.run.agent_states = state.run.agent_states || {};
    const prev = state.run.agent_states[event.agent] || {};
    const next = {
      ...prev,
      title: event.title,
      last_event_type: event.event_type,
      last_timestamp: event.timestamp,
      last_payload: event.payload,
      status: prev.status || (state.run.status === "finished" ? "finished" : "running"),
    };
    if (event.event_type === "agent.started") {
      next.status = "running";
      next.started_at = next.started_at || event.timestamp;
      next.first_started_at = next.first_started_at || event.timestamp;
    } else if (event.event_type === "agent.finished") {
      next.status = "finished";
      next.ended_at = event.timestamp;
    } else if (event.event_type === "agent.failed") {
      next.status = "failed";
      next.ended_at = event.timestamp;
    }
    state.run.agent_states[event.agent] = next;
  }

  if (event.event_type === "tree.snapshot") {
    state.latestTree = event.payload.tree;
    state.latestTreeSummary = event.payload.summary;
  }

  if (event.event_type === "check.result") {
    state.run.latest_check_result = event.payload.result;
  }
  render();
}

function render() {
  renderHero();
  renderAgentSwimlane();
  renderTraceTree();
}

function renderHero() {
  const run = state.run || {};
  els.runStatus.textContent = run.status || "待启动";
  els.currentCase.textContent = run.case_label || "未选择";
}

function shouldShowInAgentFlow(event) {
  if (!event.agent) {
    return false;
  }
  return ["agent.started", "agent.progress", "agent.finished", "agent.failed"].includes(event.event_type);
}

function getNormalizedTimelineEvent(event) {
  const payload = event.payload || {};
  const step = payload.step;
  const mode = payload.mode;
  const normalized = {
    ...event,
    laneLabel: event.agent,
    displayType: event.event_type.replace("agent.", ""),
    title: event.title || event.event_type,
    detail: "",
    status: getSwimlaneStatus(event),
    groupKey: `${event.agent}:${event.event_type}:${step || mode || event.title || ""}`,
  };

  if (event.agent === "QualityPlanner") {
    if (mode === "attach") {
      return null;
    }
    if (event.event_type === "agent.started" && mode === "plan") {
      normalized.title = "开始质量规划";
      normalized.detail = "启动质量模板裁剪、实现生成与可满足性检查";
    } else if (event.event_type === "agent.progress" && step === "quality_template_search") {
      normalized.title = "检索质量模板";
      normalized.detail = "根据当前设备筛出可实现的质量需求";
    } else if (event.event_type === "agent.progress" && step === "tap_generation") {
      normalized.title = "生成实现逻辑";
      normalized.detail = "把质量模板中的 IRP 转成候选 TAP 实现";
    } else if (event.event_type === "agent.progress" && step === "satisfaction_check") {
      normalized.title = "检查实现可满足性";
      normalized.detail = "过滤不能真正满足质量需求的实现链路";
    } else if (event.event_type === "agent.finished" && mode === "plan") {
      normalized.title = "完成质量规划";
      normalized.detail = `质量规划结果已写入追溯树，节点数 ${payload.node_count || "-"}`;
    }
  }

  if (event.agent === "IntentReader") {
    if (mode === "single") {
      return null;
    }
    if (event.event_type === "agent.started" && mode === "batch") {
      normalized.title = "开始批量解析 TAP";
      normalized.detail = `接收 ${payload.tap_count || 0} 条用户规则`;
    } else if (event.event_type === "agent.progress" && step === "tap_batch_processing") {
      normalized.title = "解析 TAP 规则";
      normalized.detail = `逐条提取设备、触发条件和行为语义，共 ${payload.tap_count || 0} 条`;
    } else if (event.event_type === "agent.progress" && step === "atom_int_extraction") {
      normalized.title = "提取原子意图";
      normalized.detail = `将每条规则中的 IRP 提取成 Atom_INT，共 ${payload.tap_count || 0} 条`;
    } else if (event.event_type === "agent.progress" && step === "intent_abstraction") {
      normalized.title = "抽象功能意图";
      normalized.detail = "把 TAP 行为归纳成更高层的用户意图";
    } else if (event.event_type === "agent.progress" && step === "intent_satisfaction_check") {
      normalized.title = "检查意图可满足性";
      normalized.detail = `已完成 ${payload.satisfied_count || 0}/${payload.result_count || 0} 条意图满足性检查`;
    } else if (event.event_type === "agent.finished" && mode === "batch") {
      normalized.title = "完成意图识别";
      normalized.detail = `识别结果已写入追溯树，节点数 ${payload.node_count || "-"}`;
    }
  }

  if (event.agent === "RealizationPlanner") {
    if (mode === "single") {
      return null;
    }
    if (event.event_type === "agent.started" && mode === "batch") {
      normalized.title = "开始实现规划";
      normalized.detail = `对 ${payload.intent_count || 0} 条最终意图生成实现方案`;
    } else if (event.event_type === "agent.progress" && step === "intent_planning") {
      normalized.title = "意图分解、规划实现与规则生成";
      normalized.detail = `对 ${payload.intent_count || 0} 条最终意图完成分解、IRP 规划与 TAP 生成`;
    } else if (event.event_type === "agent.progress" && step === "realization_satisfaction_check") {
      normalized.title = "检查实现可满足性";
      normalized.detail = `汇总校验 ${payload.intent_count || 0} 条最终意图的实现链路`;
    } else if (event.event_type === "agent.finished" && mode === "batch") {
      normalized.title = "完成实现规划";
      normalized.detail = `共生成 ${payload.result_count || 0} 组规划结果`;
    } else if (event.event_type === "agent.started" && mode === "refinement") {
      normalized.title = "开始冲突修复";
      normalized.detail = "根据检查结果修补冲突的实现链路";
    } else if (event.event_type === "agent.finished" && mode === "refinement") {
      normalized.title = "完成冲突修复";
      normalized.detail = `修复结果已回写追溯树，节点数 ${payload.node_count || "-"}`;
    }
  }

  if (event.agent === "QualityChecker") {
    if (event.event_type === "agent.started") {
      normalized.title = "开始质量检查";
      normalized.detail = "对追溯树执行 INT、IRP、DSL 三层检查";
    } else if (event.event_type === "agent.progress" && step === "int_satisfaction") {
      normalized.title = "检查 INT 满足性";
      normalized.detail = "验证功能意图是否被下游实现满足";
    } else if (event.event_type === "agent.progress" && step === "irp_conflict_check") {
      normalized.title = "检查 IRP 冲突";
      normalized.detail = "检查抽象实现规划之间是否存在冲突";
    } else if (event.event_type === "agent.progress" && step === "dsl_conflict_check") {
      normalized.title = "检查 DSL 冲突";
      normalized.detail = "检查最终 TAP 规则之间是否存在实现冲突";
    } else if (event.event_type === "agent.finished") {
      const allPass = payload.all_INT_satisfied && payload.all_IRP_ok && payload.all_TAP_ok;
      normalized.title = allPass ? "完成质量检查" : "完成检查并发现问题";
      normalized.detail = `INT:${payload.all_INT_satisfied ? "通过" : "未通过"} / IRP:${payload.all_IRP_ok ? "通过" : "未通过"} / DSL:${payload.all_TAP_ok ? "通过" : "未通过"}`;
    }
  }

  if (event.event_type === "agent.failed") {
    normalized.title = `${event.agent} 执行失败`;
    normalized.detail = payload.error || "请查看日志定位问题";
    normalized.status = "failed";
  }

  return normalized;
}

function collectTimelineEvents() {
  const timeline = [];
  for (const event of state.events) {
    if (!shouldShowInAgentFlow(event)) continue;
    const normalized = getNormalizedTimelineEvent(event);
    if (!normalized) continue;
    const prev = timeline[timeline.length - 1];
    if (
      prev &&
      prev.agent === normalized.agent &&
      prev.groupKey === normalized.groupKey &&
      prev.status === normalized.status
    ) {
      prev.timestamp = normalized.timestamp;
      prev.seq = normalized.seq;
      prev.detail = normalized.detail || prev.detail;
      continue;
    }
    timeline.push(normalized);
  }
  return timeline;
}

function getSwimlaneStatus(event) {
  if (event.event_type === "agent.finished") return "finished";
  if (event.event_type === "agent.failed") return "failed";
  if (event.event_type === "agent.started" || event.event_type === "agent.progress") return "running";
  return "pending";
}

function formatSwimlaneMeta(event) {
  const parts = [event.displayType || event.event_type.replace("agent.", "")];
  if (event.timestamp) {
    const time = new Date(event.timestamp);
    if (!Number.isNaN(time.getTime())) {
      parts.push(time.toLocaleTimeString("zh-CN", { hour12: false }));
    }
  }
  return parts.join(" | ");
}

function fmtClock(value) {
  if (!value) return "--:--:--";
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) return "--:--:--";
  return time.toLocaleTimeString("zh-CN", { hour12: false });
}

const TREE_LEVEL_LABELS = {
  ROOT: "ROOT",
  INT_TYPE: "INT_TYPE",
  INT: "INT",
  IRP: "IRP",
  DSL: "DSL",
};

function getTreeDisplayName(name) {
  return String(name || "").replace(/\s*\(副本\d+\)\s*/g, "").trim();
}

function renderAgentSwimlane() {
  const visibleEvents = collectTimelineEvents().filter((event) => AGENT_ORDER.includes(event.agent));

  if (!visibleEvents.length) {
    els.agentSwimlane.className = "swimlane empty";
    els.agentSwimlane.textContent = "等待 Agent 事件...";
    return;
  }

  const sidePadding = 72;
  const laneWidth = 156;
  const headerHeight = 112;
  const rowHeight = 116;
  const canvasWidth = Math.max(720, sidePadding + AGENT_ORDER.length * laneWidth + 24);
  const canvasHeight = Math.max(760, headerHeight + visibleEvents.length * rowHeight + 36);

  const stepHtml = visibleEvents.map((event, index) => {
    const stepTop = headerHeight + index * rowHeight + 34;
    return `
      <div class="sequence-step-line" style="top:${stepTop}px"></div>
      <div class="sequence-step-label" style="top:${stepTop}px">
        <div>#${escapeHtml(event.seq || index + 1)}</div>
        <div>${escapeHtml(fmtClock(event.timestamp))}</div>
      </div>
    `;
  }).join("");

  const laneHtml = AGENT_ORDER.map((agent, laneIndex) => {
    const info = state.run?.agent_states?.[agent] || { status: "pending" };
    const laneLeft = sidePadding + laneIndex * laneWidth;
    const nodeHtml = visibleEvents.map((event, eventIndex) => {
      if (event.agent !== agent) return "";
      const top = headerHeight + eventIndex * rowHeight + 14;
      return `
        <div class="sequence-node ${escapeHtml(getSwimlaneStatus(event))}" style="top:${top}px">
          <div class="sequence-node-dot"></div>
          <div class="type-badge">${escapeHtml(event.displayType || event.event_type)}</div>
          <div class="sequence-node-title">${escapeHtml(event.title || event.event_type)}</div>
          <div class="sequence-node-meta">${escapeHtml(event.detail || formatSwimlaneMeta(event))}</div>
        </div>
      `;
    }).join("");

    return `
      <section class="sequence-lane" style="left:${laneLeft}px;width:${laneWidth}px">
        <div class="sequence-lane-line"></div>
        <div class="sequence-lane-header">
          <strong>${escapeHtml(agent)}</strong>
          <span>${escapeHtml(AGENT_DESCRIPTIONS[agent] || "")}</span>
          <span><span class="status-pill ${escapeHtml(info.status || "pending")}">${escapeHtml(info.status || "pending")}</span></span>
        </div>
        ${nodeHtml}
      </section>
    `;
  }).join("");

  els.agentSwimlane.className = "swimlane";
  els.agentSwimlane.innerHTML = `
    <div class="swimlane-canvas" style="width:${canvasWidth}px;height:${canvasHeight}px">
      ${stepHtml}
      ${laneHtml}
    </div>
  `;
}

function buildTreeLayout(tree) {
  const nodes = Array.isArray(tree?.nodes) ? tree.nodes : [];
  const edges = Array.isArray(tree?.edges) ? tree.edges : [];
  const indexMap = new Map(nodes.map((node) => [node.id, node]));
  const nodeByName = new Map(nodes.map((node) => [node.name, node]));
  const childMap = new Map();
  const parentMap = new Map();
  const rootName = tree.root || nodes[0]?.name;

  edges.forEach((edge) => {
    const parent = indexMap.get(edge.from);
    const child = indexMap.get(edge.to);
    if (!parent || !child) return;
    const children = childMap.get(parent.name) || [];
    children.push(child.name);
    childMap.set(parent.name, children);
    parentMap.set(child.name, parent.name);
  });

  childMap.forEach((children, key) => {
    children.sort((a, b) => String(a).localeCompare(String(b), "zh-CN"));
    childMap.set(key, children);
  });

  const bfsOrder = [];
  const bfsIndexMap = new Map();
  const queue = [];

  if (rootName) {
    queue.push(rootName);
  }

  while (queue.length) {
    const currentName = queue.shift();
    if (!bfsIndexMap.has(currentName)) {
      bfsIndexMap.set(currentName, bfsOrder.length);
      bfsOrder.push(currentName);
    }
    for (const childName of childMap.get(currentName) || []) {
      if (!bfsIndexMap.has(childName) && !queue.includes(childName)) {
        queue.push(childName);
      }
    }
  }

  for (const node of nodes) {
    if (!bfsIndexMap.has(node.name)) {
      bfsIndexMap.set(node.name, bfsOrder.length);
      bfsOrder.push(node.name);
    }
  }

  const intLevelMap = new Map();

  function computeIntLevel(nodeName) {
    if (intLevelMap.has(nodeName)) {
      return intLevelMap.get(nodeName);
    }
    const node = nodeByName.get(nodeName);
    if (!node) {
      return 0;
    }
    if (node.type === "ROOT") {
      intLevelMap.set(nodeName, 0);
      return 0;
    }
    if (node.type === "INT_TYPE") {
      intLevelMap.set(nodeName, 1);
      return 1;
    }
    if (node.type === "INT") {
      const parentName = parentMap.get(nodeName);
      const parentLevel = parentName ? computeIntLevel(parentName) : 1;
      const level = Math.max(2, parentLevel + 1);
      intLevelMap.set(nodeName, level);
      return level;
    }
    const parentName = parentMap.get(nodeName);
    const inherited = parentName ? computeIntLevel(parentName) : 1;
    intLevelMap.set(nodeName, inherited);
    return inherited;
  }

  let maxIntLevel = 1;
  nodes.forEach((node) => {
    if (node.type === "INT") {
      maxIntLevel = Math.max(maxIntLevel, computeIntLevel(node.name));
    }
  });

  const irpLevel = maxIntLevel + 1;
  const dslLevel = maxIntLevel + 2;
  const assignedLevelMap = new Map();

  nodes.forEach((node) => {
    let level = 0;
    if (node.type === "ROOT") {
      level = 0;
    } else if (node.type === "INT_TYPE") {
      level = 1;
    } else if (node.type === "INT") {
      level = computeIntLevel(node.name);
    } else if (node.type === "IRP") {
      level = irpLevel;
    } else if (node.type === "DSL") {
      level = dslLevel;
    } else {
      const parentName = parentMap.get(node.name);
      level = parentName ? computeIntLevel(parentName) + 1 : dslLevel;
    }
    assignedLevelMap.set(node.name, level);
  });

  const levelNameBuckets = new Map();
  nodes.forEach((node) => {
    const level = assignedLevelMap.get(node.name) ?? 0;
    const bucket = levelNameBuckets.get(level) || [];
    bucket.push(node.name);
    levelNameBuckets.set(level, bucket);
  });

  const visibleLevels = Array.from(levelNameBuckets.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([depth, names]) => ({
      depth,
      nodes: (names || []).map((name) => nodeByName.get(name)).filter(Boolean),
    }))
    .filter((level) => level.nodes.length > 0);

  visibleLevels.forEach((level) => {
    level.nodes.sort((a, b) => {
      const orderA = bfsIndexMap.get(a.name) ?? Number.MAX_SAFE_INTEGER;
      const orderB = bfsIndexMap.get(b.name) ?? Number.MAX_SAFE_INTEGER;
      if (orderA !== orderB) return orderA - orderB;
      return String(a.name).localeCompare(String(b.name), "zh-CN");
    });
  });

  const nodeWidth = 190;
  const nodeHeight = 64;
  const topMargin = 70;
  const leftMargin = 170;
  const horizontalGap = 28;
  const levelGap = 122;
  const maxLevelWidth = Math.max(...visibleLevels.map((level) => level.nodes.length), 1);
  const width = Math.max(960, leftMargin * 2 + maxLevelWidth * nodeWidth + (maxLevelWidth - 1) * horizontalGap);
  const levelDepths = visibleLevels.map((level) => level.depth);
  const maxDepth = levelDepths.length ? Math.max(...levelDepths) : 0;
  const height = Math.max(760, topMargin + (maxDepth + 1) * levelGap + 80);

  const positions = new Map();
  visibleLevels.forEach((level) => {
    const totalWidth = level.nodes.length * nodeWidth + Math.max(0, level.nodes.length - 1) * horizontalGap;
    const startLeft = Math.max(leftMargin, (width - totalWidth) / 2);
    level.nodes.forEach((node, nodeIndex) => {
      positions.set(node.name, {
        left: startLeft + nodeIndex * (nodeWidth + horizontalGap),
        top: topMargin + level.depth * levelGap,
        depth: level.depth,
        order: nodeIndex + 1,
      });
    });
  });

  return {
    nodes,
    edges,
    levels: visibleLevels,
    childMap,
    rootName,
    positions,
    width,
    height,
    nodeWidth,
    nodeHeight,
    irpLevel,
    dslLevel,
  };
}

function buildEdgePath(fromPos, toPos, nodeWidth, nodeHeight) {
  if (!fromPos || !toPos) return "";
  const startX = fromPos.left + nodeWidth / 2;
  const startY = fromPos.top + nodeHeight;
  const endX = toPos.left + nodeWidth / 2;
  const endY = toPos.top;
  const midY = startY + Math.max(28, (endY - startY) * 0.46);
  return `M ${startX} ${startY} C ${startX} ${midY}, ${endX} ${midY}, ${endX} ${endY}`;
}

function renderTraceTree() {
  const tree = state.latestTree;
  if (!tree || !Array.isArray(tree.nodes) || !tree.nodes.length) {
    els.traceTree.className = "trace-tree empty";
    els.traceTree.textContent = "等待追溯树事件...";
    return;
  }

  const newNodeNames = new Set(state.latestTreeSummary?.added_node_names || []);
  const layout = buildTreeLayout(tree);
  const levelLabels = layout.levels.map((level) => {
    const top = 26 + level.depth * 122;
    let labelType = level.nodes[0]?.type || "";
    if (level.depth >= 2 && level.depth < layout.irpLevel) {
      labelType = "INT";
    } else if (level.depth === layout.irpLevel) {
      labelType = "IRP";
    } else if (level.depth === layout.dslLevel) {
      labelType = "DSL";
    }
    const label = TREE_LEVEL_LABELS[labelType] || labelType || "UNKNOWN";
    return `<div class="tree-level-label" style="top:${top}px">Level ${level.depth} · ${escapeHtml(label)}</div>`;
  }).join("");

  const pathHtml = layout.edges.map((edge) => {
    const fromNode = layout.nodes.find((node) => node.id === edge.from);
    const toNode = layout.nodes.find((node) => node.id === edge.to);
    if (!fromNode || !toNode) return "";
    const fromPos = layout.positions.get(fromNode.name);
    const toPos = layout.positions.get(toNode.name);
    const path = buildEdgePath(fromPos, toPos, layout.nodeWidth, layout.nodeHeight);
    if (!path) return "";
    const sameDepth = fromPos && toPos && fromPos.depth === toPos.depth;
    return `<path class="paper-tree-path ${sameDepth ? "paper-tree-same-lane" : ""}" d="${path}"></path>`;
  }).join("");

  const nodeHtml = layout.levels.map((level) => level.nodes.map((node) => {
    const pos = layout.positions.get(node.name);
    const isNew = newNodeNames.has(node.name);
    return `
      <div class="tree-node-card ${isNew ? "is-new" : ""}" style="left:${pos.left}px;top:${pos.top}px">
        <div class="tree-node-meta">
          <span class="type-badge">${escapeHtml(node.type)}</span>
          <span class="tree-node-order">#${pos.order}</span>
        </div>
        <div class="tree-node-name">${escapeHtml(getTreeDisplayName(node.name))}</div>
      </div>
    `;
  }).join("")).join("");

  els.traceTree.className = "trace-tree";
  els.traceTree.innerHTML = `
    <div class="paper-tree-canvas" style="width:${layout.width}px;height:${layout.height}px">
      ${levelLabels}
      <svg class="paper-tree-svg" viewBox="0 0 ${layout.width} ${layout.height}" preserveAspectRatio="none">
        ${pathHtml}
      </svg>
      ${nodeHtml}
    </div>
  `;
}


els.refreshCasesBtn.addEventListener("click", loadCases);
els.startRunBtn.addEventListener("click", startRun);

loadCases().then(render);
