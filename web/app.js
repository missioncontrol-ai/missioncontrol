const API_BASE = window.location.origin;
const TOKEN_KEY = "missioncontrol_token";
const COLLAPSE_KEY = "missioncontrol_tree_collapsed";
const ONBOARDING_ENDPOINT_KEY = "missioncontrol_onboarding_endpoint";
const EXPLORER_VIEW_KEY = "missioncontrol_explorer_view";
const nodeState = { type: null, id: null };
const collapsedNodes = new Set();
let authReady = false;

const appShellEl = document.getElementById("app-shell");
const loginGateEl = document.getElementById("login-gate");
const loginTokenInput = document.getElementById("login-token-input");
const loginSubmitBtn = document.getElementById("login-submit");
const loginErrorEl = document.getElementById("login-error");
const summaryEl = document.getElementById("summary");
const filtersBarEl = document.getElementById("filters-bar");
const treeEl = document.getElementById("tree-root");
const detailEl = document.getElementById("detail-root");
const errorEl = document.getElementById("error-banner");
const tokenInput = document.getElementById("token-input");
const searchInput = document.getElementById("search-input");
const statusFilter = document.getElementById("status-filter");
const treeViewFilter = document.getElementById("tree-view-filter");
const statusFilterLabel = statusFilter?.closest("label");
const explorerView = document.getElementById("explorer-view");
const onboardingView = document.getElementById("onboarding-view");
const adminView = document.getElementById("admin-view");
const tabExplorer = document.getElementById("tab-explorer");
const tabOnboarding = document.getElementById("tab-onboarding");
const tabAdmin = document.getElementById("tab-admin");
const logoutBtn = document.getElementById("logout-btn");
const manifestUrlEl = document.getElementById("manifest-url");
const manifestJsonEl = document.getElementById("manifest-json");
const mcpConfigEl = document.getElementById("mcp-config");
const bootstrapCommandsEl = document.getElementById("bootstrap-commands");
const onboardingEndpointInput = document.getElementById("onboarding-endpoint-input");
const governanceActiveEl = document.getElementById("governance-active");
const governanceDraftJsonEl = document.getElementById("governance-draft-json");
const governanceVersionsEl = document.getElementById("governance-versions");
const governanceEventsEl = document.getElementById("governance-events");
let currentGovernanceDraftId = null;

function showError(message) {
  errorEl.textContent = message;
  errorEl.classList.remove("hidden");
}

function hideError() {
  errorEl.textContent = "";
  errorEl.classList.add("hidden");
}

function showLoginError(message) {
  loginErrorEl.textContent = message;
  loginErrorEl.classList.remove("hidden");
}

function hideLoginError() {
  loginErrorEl.textContent = "";
  loginErrorEl.classList.add("hidden");
}

function showLoginGate() {
  authReady = false;
  appShellEl.classList.add("hidden");
  loginGateEl.classList.remove("hidden");
  hideError();
}

function showAppShell() {
  authReady = true;
  hideLoginError();
  loginGateEl.classList.add("hidden");
  appShellEl.classList.remove("hidden");
}

function currentToken() {
  return tokenInput.value.trim();
}

function setToken(value) {
  const v = value || "";
  tokenInput.value = v;
  loginTokenInput.value = v;
}

function authHeaders() {
  const token = currentToken();
  if (!token) {
    return {};
  }
  return { Authorization: `Bearer ${token}` };
}

async function fetchJSON(path) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      Accept: "application/json",
      ...authHeaders(),
    },
  });
  const responseUrl = String(res.url || "");
  if (responseUrl.includes("cloudflareaccess.com")) {
    throw new Error("Cloudflare Access is intercepting API calls. Disable/bypass Access for mc.example.com.");
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json();
}

async function fetchJSONWithMethod(path, method, payload) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const responseUrl = String(res.url || "");
  if (responseUrl.includes("cloudflareaccess.com")) {
    throw new Error("Cloudflare Access is intercepting API calls. Disable/bypass Access for mc.example.com.");
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json();
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function statusChip(status, count) {
  return `<span class="chip">${status}:${count}</span>`;
}

function normalizeExplorerTree(tree) {
  const normalizeKluster = (kluster) => ({
    ...kluster,
    task_status_counts: kluster?.task_status_counts || {},
    recent_tasks: kluster?.recent_tasks || [],
  });
  const normalizeMission = (mission) => {
    const klusters = (mission?.klusters || []).map(normalizeKluster);
    return {
      ...mission,
      kluster_count: mission?.kluster_count ?? klusters.length,
      klusters,
    };
  };

  const missions = (tree?.missions || []).map(normalizeMission);
  const unassignedKlusters = (tree?.unassigned_klusters || []).map(normalizeKluster);
  const fallbackKlusterCount =
    missions.reduce((sum, mission) => sum + (mission.kluster_count || 0), 0) + unassignedKlusters.length;
  const taskCount =
    tree?.task_count ??
    missions.reduce((sum, mission) => sum + (mission.task_count || 0), 0) +
      unassignedKlusters.reduce((sum, kluster) => sum + (kluster.task_count || 0), 0);

  return {
    ...tree,
    mission_count: tree?.mission_count ?? missions.length,
    kluster_count: tree?.kluster_count ?? fallbackKlusterCount,
    task_count: taskCount,
    missions,
    unassigned_klusters: unassignedKlusters,
  };
}

function nodeKey(type, id) {
  return `${type}:${id}`;
}

function loadCollapsedNodes() {
  const raw = window.localStorage.getItem(COLLAPSE_KEY);
  if (!raw) return;
  try {
    const values = JSON.parse(raw);
    if (Array.isArray(values)) {
      values.forEach((v) => collapsedNodes.add(String(v)));
    }
  } catch (_) {
    // ignore invalid local storage payload
  }
}

function saveCollapsedNodes() {
  window.localStorage.setItem(COLLAPSE_KEY, JSON.stringify(Array.from(collapsedNodes)));
}

function isCollapsed(type, id) {
  return collapsedNodes.has(nodeKey(type, id));
}

function setCollapsed(type, id, collapsed) {
  const key = nodeKey(type, id);
  if (collapsed) {
    collapsedNodes.add(key);
  } else {
    collapsedNodes.delete(key);
  }
  saveCollapsedNodes();
}

function currentTreeView() {
  return treeViewFilter.value || "tasks";
}

function ownerList(value) {
  return String(value || "")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

function renderSummary(tree, context = {}) {
  const view = currentTreeView();
  let thirdMetricLabel = "Tasks";
  let thirdMetricValue = tree.task_count;
  if (view === "artifacts") {
    thirdMetricLabel = "Artifacts";
    thirdMetricValue = context.artifact_count ?? 0;
  } else if (view === "governance") {
    thirdMetricLabel = "Policy";
    thirdMetricValue = context.policy_label || "n/a";
  }
  summaryEl.innerHTML = `
    <div class="metric">Missions <strong>${tree.mission_count}</strong></div>
    <div class="metric">Klusters <strong>${tree.kluster_count}</strong></div>
    <div class="metric">${thirdMetricLabel} <strong>${thirdMetricValue}</strong></div>
    <div class="metric">Generated <strong>${formatDate(tree.generated_at)}</strong></div>
  `;
}

function bindTreeEvents() {
  treeEl.querySelectorAll("[data-type][data-id]").forEach((el) => {
    el.addEventListener("click", async (event) => {
      event.preventDefault();
      const type = el.dataset.type;
      const id = el.dataset.id;
      if (!type || !id) return;
      await loadNode(type, id);
    });
  });

  treeEl.querySelectorAll("[data-toggle-type][data-toggle-id]").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const type = el.dataset.toggleType;
      const id = el.dataset.toggleId;
      if (!type || !id) return;
      const parentNode = el.closest(".node");
      if (!parentNode) return;
      const childList =
        parentNode.querySelector(":scope > .kluster-list") || parentNode.querySelector(":scope > .task-list");
      if (!childList) return;
      childList.classList.toggle("hidden");
      const nowCollapsed = childList.classList.contains("hidden");
      setCollapsed(type, id, nowCollapsed);
      el.textContent = nowCollapsed ? "▸" : "▾";
    });
  });
}

function renderTaskTree(tree) {
  treeEl.innerHTML = "";
  if (!tree.missions.length && !tree.unassigned_klusters.length) {
    treeEl.innerHTML = "<div class='empty'>No matching entities.</div>";
    return;
  }

  tree.missions.forEach((mission) => {
    const missionCollapsed = isCollapsed("mission", mission.id);
    const missionNode = document.createElement("div");
    missionNode.className = "node mission";
    missionNode.innerHTML = `
      <div class="node-header">
        <button class="node-toggle" data-toggle-type="mission" data-toggle-id="${mission.id}">
          ${missionCollapsed ? "▸" : "▾"}
        </button>
        <button class="node-title" data-type="mission" data-id="${mission.id}">
          Mission ${mission.name} (${mission.kluster_count} klusters, ${mission.task_count} tasks)
        </button>
      </div>
    `;
    const klusterList = document.createElement("div");
    klusterList.className = "kluster-list";
    if (missionCollapsed) {
      klusterList.classList.add("hidden");
    }
    mission.klusters.forEach((kluster) => {
      const klusterCollapsed = isCollapsed("kluster", kluster.id);
      const klusterNode = document.createElement("div");
      klusterNode.className = "node kluster";
      const statusCounts = Object.entries(kluster.task_status_counts)
        .map(([status, count]) => statusChip(status, count))
        .join(" ");
      klusterNode.innerHTML = `
        <div class="node-header">
          <button class="node-toggle" data-toggle-type="kluster" data-toggle-id="${kluster.id}">
            ${klusterCollapsed ? "▸" : "▾"}
          </button>
          <button class="node-title" data-type="kluster" data-id="${kluster.id}">
            Kluster ${kluster.name} (${kluster.task_count} tasks)
          </button>
        </div>
        <div class="node-meta">${statusCounts || "<span class='chip'>no_tasks:0</span>"}</div>
      `;
      const taskList = document.createElement("div");
      taskList.className = "task-list";
      if (klusterCollapsed) {
        taskList.classList.add("hidden");
      }
      kluster.recent_tasks.forEach((task) => {
        const taskNode = document.createElement("button");
        taskNode.className = "task-row";
        taskNode.dataset.type = "task";
        taskNode.dataset.id = String(task.id);
        taskNode.textContent = `${task.title} [${task.status}]`;
        taskList.appendChild(taskNode);
      });
      klusterNode.appendChild(taskList);
      klusterList.appendChild(klusterNode);
    });
    missionNode.appendChild(klusterList);
    treeEl.appendChild(missionNode);
  });

  if (tree.unassigned_klusters.length) {
    const unassigned = document.createElement("div");
    unassigned.className = "node mission";
    unassigned.innerHTML = "<h3>Unassigned Klusters</h3>";
    tree.unassigned_klusters.forEach((kluster) => {
      const klusterCollapsed = isCollapsed("kluster", kluster.id);
      const statusCounts = Object.entries(kluster.task_status_counts)
        .map(([status, count]) => statusChip(status, count))
        .join(" ");
      const klusterNode = document.createElement("div");
      klusterNode.className = "node kluster";
      klusterNode.innerHTML = `
        <div class="node-header">
          <button class="node-toggle" data-toggle-type="kluster" data-toggle-id="${kluster.id}">
            ${klusterCollapsed ? "▸" : "▾"}
          </button>
          <button class="node-title" data-type="kluster" data-id="${kluster.id}">
            Kluster ${kluster.name} (${kluster.task_count} tasks)
          </button>
        </div>
        <div class="node-meta">${statusCounts || "<span class='chip'>no_tasks:0</span>"}</div>
      `;
      const taskList = document.createElement("div");
      taskList.className = "task-list";
      if (klusterCollapsed) {
        taskList.classList.add("hidden");
      }
      kluster.recent_tasks.forEach((task) => {
        const taskNode = document.createElement("button");
        taskNode.className = "task-row";
        taskNode.dataset.type = "task";
        taskNode.dataset.id = String(task.id);
        taskNode.textContent = `${task.title} [${task.status}]`;
        taskList.appendChild(taskNode);
      });
      klusterNode.appendChild(taskList);
      unassigned.appendChild(klusterNode);
    });
    treeEl.appendChild(unassigned);
  }

  bindTreeEvents();
}

function renderArtifactTree(tree, context) {
  const artifactsByKluster = context.artifacts_by_kluster || {};
  treeEl.innerHTML = "";
  if (!tree.missions.length && !tree.unassigned_klusters.length) {
    treeEl.innerHTML = "<div class='empty'>No matching entities.</div>";
    return;
  }

  const artifactStatusChips = (artifacts) => {
    const counts = {};
    artifacts.forEach((artifact) => {
      const key = artifact.status || "unknown";
      counts[key] = (counts[key] || 0) + 1;
    });
    return Object.entries(counts)
      .map(([status, count]) => statusChip(status, count))
      .join(" ");
  };

  const appendKluster = (parent, kluster) => {
    const klusterArtifacts = artifactsByKluster[String(kluster.id)] || [];
    const klusterCollapsed = isCollapsed("kluster", kluster.id);
    const klusterNode = document.createElement("div");
    klusterNode.className = "node kluster";
    klusterNode.innerHTML = `
      <div class="node-header">
        <button class="node-toggle" data-toggle-type="kluster" data-toggle-id="${kluster.id}">
          ${klusterCollapsed ? "▸" : "▾"}
        </button>
        <button class="node-title" data-type="kluster" data-id="${kluster.id}">
          Kluster ${kluster.name} (${klusterArtifacts.length} artifacts)
        </button>
      </div>
      <div class="node-meta">${artifactStatusChips(klusterArtifacts) || "<span class='chip'>no_artifacts:0</span>"}</div>
    `;
    const artifactList = document.createElement("div");
    artifactList.className = "task-list";
    if (klusterCollapsed) {
      artifactList.classList.add("hidden");
    }
    klusterArtifacts.forEach((artifact) => {
      const artifactNode = document.createElement("button");
      artifactNode.className = "task-row";
      artifactNode.dataset.type = "artifact";
      artifactNode.dataset.id = String(artifact.id);
      artifactNode.textContent = `${artifact.name} [${artifact.status}]`;
      artifactList.appendChild(artifactNode);
    });
    klusterNode.appendChild(artifactList);
    parent.appendChild(klusterNode);
  };

  tree.missions.forEach((mission) => {
    const missionCollapsed = isCollapsed("mission", mission.id);
    const missionArtifactCount = mission.klusters.reduce(
      (sum, kluster) => sum + (artifactsByKluster[String(kluster.id)] || []).length,
      0
    );
    const missionNode = document.createElement("div");
    missionNode.className = "node mission";
    missionNode.innerHTML = `
      <div class="node-header">
        <button class="node-toggle" data-toggle-type="mission" data-toggle-id="${mission.id}">
          ${missionCollapsed ? "▸" : "▾"}
        </button>
        <button class="node-title" data-type="mission" data-id="${mission.id}">
          Mission ${mission.name} (${mission.kluster_count} klusters, ${missionArtifactCount} artifacts)
        </button>
      </div>
    `;
    const klusterList = document.createElement("div");
    klusterList.className = "kluster-list";
    if (missionCollapsed) {
      klusterList.classList.add("hidden");
    }
    mission.klusters.forEach((kluster) => appendKluster(klusterList, kluster));
    missionNode.appendChild(klusterList);
    treeEl.appendChild(missionNode);
  });

  if (tree.unassigned_klusters.length) {
    const unassigned = document.createElement("div");
    unassigned.className = "node mission";
    unassigned.innerHTML = "<h3>Unassigned Klusters</h3>";
    tree.unassigned_klusters.forEach((kluster) => appendKluster(unassigned, kluster));
    treeEl.appendChild(unassigned);
  }
  bindTreeEvents();
}

function renderGovernanceTree(tree, context) {
  const rolesByMission = context.roles_by_mission || {};
  const skillsByKluster = context.skills_by_kluster || {};
  const policy = context.policy || null;
  treeEl.innerHTML = "";
  if (!tree.missions.length && !tree.unassigned_klusters.length) {
    treeEl.innerHTML = "<div class='empty'>No matching entities.</div>";
    return;
  }

  const rolesLine = (missionId) => {
    const roles = rolesByMission[String(missionId)] || [];
    const owners = roles.filter((r) => r.role === "mission_owner").map((r) => r.subject);
    const contributors = roles.filter((r) => r.role === "mission_contributor").map((r) => r.subject);
    return `owners:${owners.length} contributors:${contributors.length}`;
  };

  const appendKluster = (parent, kluster) => {
    const klusterCollapsed = isCollapsed("kluster", kluster.id);
    const skills = skillsByKluster[String(kluster.id)] || null;
    const owners = ownerList(kluster.owners);
    const klusterNode = document.createElement("div");
    klusterNode.className = "node kluster";
    klusterNode.innerHTML = `
      <div class="node-header">
        <button class="node-toggle" data-toggle-type="kluster" data-toggle-id="${kluster.id}">
          ${klusterCollapsed ? "▸" : "▾"}
        </button>
        <button class="node-title" data-type="kluster" data-id="${kluster.id}">
          Kluster ${kluster.name}
        </button>
      </div>
      <div class="node-meta">
        <span class="chip">policy:${policy ? `v${policy.version}` : "n/a"}</span>
        <span class="chip">skills:${skills ? `v${skills.effective_version}` : "n/a"}</span>
        <span class="chip">owners:${owners.length}</span>
      </div>
    `;
    const governanceList = document.createElement("div");
    governanceList.className = "task-list";
    if (klusterCollapsed) {
      governanceList.classList.add("hidden");
    }
    governanceList.innerHTML = `
      <div class="detail-empty">Users: ${owners.length ? owners.join(", ") : "-"}</div>
      <div class="detail-empty">Policy: ${policy ? `${policy.state} v${policy.version}` : "unavailable"}</div>
      <div class="detail-empty">Skills: ${skills ? `effective v${skills.effective_version}` : "unavailable"}</div>
    `;
    klusterNode.appendChild(governanceList);
    parent.appendChild(klusterNode);
  };

  tree.missions.forEach((mission) => {
    const missionCollapsed = isCollapsed("mission", mission.id);
    const missionNode = document.createElement("div");
    missionNode.className = "node mission";
    missionNode.innerHTML = `
      <div class="node-header">
        <button class="node-toggle" data-toggle-type="mission" data-toggle-id="${mission.id}">
          ${missionCollapsed ? "▸" : "▾"}
        </button>
        <button class="node-title" data-type="mission" data-id="${mission.id}">
          Mission ${mission.name} (${rolesLine(mission.id)})
        </button>
      </div>
    `;
    const klusterList = document.createElement("div");
    klusterList.className = "kluster-list";
    if (missionCollapsed) {
      klusterList.classList.add("hidden");
    }
    mission.klusters.forEach((kluster) => appendKluster(klusterList, kluster));
    missionNode.appendChild(klusterList);
    treeEl.appendChild(missionNode);
  });

  if (tree.unassigned_klusters.length) {
    const unassigned = document.createElement("div");
    unassigned.className = "node mission";
    unassigned.innerHTML = "<h3>Unassigned Klusters</h3>";
    tree.unassigned_klusters.forEach((kluster) => appendKluster(unassigned, kluster));
    treeEl.appendChild(unassigned);
  }
  bindTreeEvents();
}

function renderTree(tree, context = {}) {
  const view = currentTreeView();
  if (view === "artifacts") {
    renderArtifactTree(tree, context);
    return;
  }
  if (view === "governance") {
    renderGovernanceTree(tree, context);
    return;
  }
  renderTaskTree(tree);
}

function renderDetail(data) {
  const mission = data.mission
    ? `<div class="detail-card"><h3>Mission</h3><p><strong>${data.mission.name}</strong></p><p>ID: ${data.mission.id}</p><p>Status: ${data.mission.status}</p><p>Owners: ${data.mission.owners || "-"}</p></div>`
    : "";
  const kluster = data.kluster
    ? `<div class="detail-card"><h3>Kluster</h3><p><strong>${data.kluster.name}</strong></p><p>ID: ${data.kluster.id}</p><p>Mission: ${data.kluster.mission_id || "none"}</p><p>Status: ${data.kluster.status}</p></div>`
    : "";
  const task = data.task
    ? `<div class="detail-card"><h3>Task</h3><p><strong>${data.task.title}</strong></p><p>ID: ${data.task.id}</p><p>Status: ${data.task.status}</p><p>Owner: ${data.task.owner || "-"}</p><p>${data.task.description || ""}</p></div>`
    : "";
  const artifact = data.artifact
    ? `<div class="detail-card"><h3>Artifact</h3><p><strong>${data.artifact.name}</strong></p><p>ID: ${data.artifact.id}</p><p>Kluster: ${data.artifact.kluster_id || "-"}</p><p>Type: ${data.artifact.artifact_type || "-"}</p><p>Status: ${data.artifact.status || "-"}</p><p>URI: ${data.artifact.uri || "-"}</p></div>`
    : "";
  const taskRows = (data.tasks || [])
    .map(
      (item) =>
        `<tr><td>${item.id}</td><td>${item.title}</td><td>${item.status}</td><td>${item.owner || "-"}</td><td>${formatDate(
          item.updated_at
        )}</td></tr>`
    )
    .join("");
  const taskTable = taskRows
    ? `<div class="detail-card"><h3>Recent Tasks</h3><table><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Owner</th><th>Updated</th></tr></thead><tbody>${taskRows}</tbody></table></div>`
    : "";
  detailEl.innerHTML = `${mission}${kluster}${task}${artifact}${taskTable}` || "<div class='detail-empty'>No details available.</div>";
}

function treeQueryString() {
  const params = new URLSearchParams();
  const q = searchInput.value.trim();
  const view = currentTreeView();
  const status = statusFilter.value;
  if (q) params.set("q", q);
  if (view === "tasks" && status) params.set("status", status);
  return params.toString();
}

async function loadArtifactTreeContext(tree) {
  const artifacts = await fetchJSON("/artifacts");
  const klusterIdSet = new Set();
  tree.missions.forEach((mission) => mission.klusters.forEach((kluster) => klusterIdSet.add(String(kluster.id))));
  tree.unassigned_klusters.forEach((kluster) => klusterIdSet.add(String(kluster.id)));
  const artifactsByKluster = {};
  let artifactCount = 0;
  artifacts.forEach((artifact) => {
    const klusterId = String(artifact.kluster_id || "");
    if (!klusterIdSet.has(klusterId)) return;
    artifactCount += 1;
    artifactsByKluster[klusterId] = artifactsByKluster[klusterId] || [];
    artifactsByKluster[klusterId].push(artifact);
  });
  return { artifacts_by_kluster: artifactsByKluster, artifact_count: artifactCount };
}

async function loadGovernanceTreeContext(tree) {
  const missionIds = Array.from(new Set(tree.missions.map((mission) => String(mission.id))));
  const klusters = [];
  tree.missions.forEach((mission) => {
    mission.klusters.forEach((kluster) => klusters.push({ mission_id: mission.id, id: kluster.id }));
  });
  const policyRes = await Promise.allSettled([fetchJSON("/governance/policy/active")]);
  const policy = policyRes[0].status === "fulfilled" ? policyRes[0].value : null;

  const roleRequests = missionIds.map((missionId) => fetchJSON(`/missions/${encodeURIComponent(missionId)}/roles`));
  const roleResults = await Promise.allSettled(roleRequests);
  const rolesByMission = {};
  missionIds.forEach((missionId, idx) => {
    const result = roleResults[idx];
    rolesByMission[missionId] = result.status === "fulfilled" ? result.value : [];
  });

  const skillsRequests = klusters.map((kluster) =>
    fetchJSON(
      `/skills/snapshots/resolve?mission_id=${encodeURIComponent(kluster.mission_id)}&kluster_id=${encodeURIComponent(kluster.id)}`
    )
  );
  const skillsResults = await Promise.allSettled(skillsRequests);
  const skillsByKluster = {};
  klusters.forEach((kluster, idx) => {
    const result = skillsResults[idx];
    if (result.status === "fulfilled") {
      skillsByKluster[String(kluster.id)] = result.value;
    }
  });

  return {
    roles_by_mission: rolesByMission,
    skills_by_kluster: skillsByKluster,
    policy,
    policy_label: policy ? `v${policy.version}` : "n/a",
  };
}

function syncViewFilters() {
  const view = currentTreeView();
  if (statusFilterLabel) {
    statusFilterLabel.classList.toggle("hidden", view !== "tasks");
  }
  window.localStorage.setItem(EXPLORER_VIEW_KEY, view);
}

async function loadTree() {
  hideError();
  const query = treeQueryString();
  const path = query ? `/explorer/tree?${query}` : "/explorer/tree";
  const rawTree = await fetchJSON(path);
  const tree = normalizeExplorerTree(rawTree);
  let context = {};
  const view = currentTreeView();
  if (view === "artifacts") {
    context = await loadArtifactTreeContext(tree);
  } else if (view === "governance") {
    context = await loadGovernanceTreeContext(tree);
  }
  renderSummary(tree, context);
  renderTree(tree, context);
}

async function loadOnboardingManifest() {
  hideError();
  const endpoint = currentOnboardingEndpoint();
  const query = new URLSearchParams({ endpoint }).toString();
  const manifest = await fetchJSON(`/agent-onboarding.json?${query}`);
  manifestUrlEl.textContent = `${endpoint}/agent-onboarding.json`;
  manifestJsonEl.textContent = JSON.stringify(manifest, null, 2);
  mcpConfigEl.textContent = JSON.stringify({ missioncontrol: manifest.mcp_server }, null, 2);
  bootstrapCommandsEl.textContent = `${manifest.bootstrap.remote_script}\n${manifest.bootstrap.local_script}`;
}

function normalizeOnboardingEndpoint(rawValue) {
  const trimmed = String(rawValue || "").trim();
  if (!trimmed) {
    return API_BASE.replace(/\/$/, "");
  }
  const withScheme = trimmed.includes("://") ? trimmed : `https://${trimmed}`;
  let parsed;
  try {
    parsed = new URL(withScheme);
  } catch (_) {
    throw new Error("Invalid endpoint. Enter a hostname or full URL.");
  }
  if (!parsed.hostname) {
    throw new Error("Invalid endpoint. Enter a hostname or full URL.");
  }
  return `${parsed.protocol}//${parsed.host}`.replace(/\/$/, "");
}

function currentOnboardingEndpoint() {
  const endpoint = normalizeOnboardingEndpoint(onboardingEndpointInput.value);
  onboardingEndpointInput.value = endpoint;
  window.localStorage.setItem(ONBOARDING_ENDPOINT_KEY, endpoint);
  return endpoint;
}

function initOnboardingEndpoint() {
  const stored = window.localStorage.getItem(ONBOARDING_ENDPOINT_KEY);
  onboardingEndpointInput.value = stored || API_BASE;
}

async function loadGovernance() {
  hideError();
  const active = await fetchJSON("/governance/policy/active");
  governanceActiveEl.textContent = JSON.stringify(active, null, 2);
  const versions = await fetchJSON("/governance/policy/versions");
  governanceVersionsEl.textContent = JSON.stringify(versions, null, 2);
  const events = await fetchJSON("/governance/policy/events?limit=100");
  governanceEventsEl.textContent = JSON.stringify(events, null, 2);

  const draft = versions.find((v) => v.state === "draft");
  if (draft) {
    currentGovernanceDraftId = draft.id;
    governanceDraftJsonEl.value = JSON.stringify(draft.policy, null, 2);
  } else {
    currentGovernanceDraftId = null;
    governanceDraftJsonEl.value = JSON.stringify(active.policy, null, 2);
  }
}

async function createGovernanceDraftFromCurrent() {
  let policy = null;
  if (governanceDraftJsonEl.value.trim()) {
    policy = JSON.parse(governanceDraftJsonEl.value);
  }
  const created = await fetchJSONWithMethod("/governance/policy/drafts", "POST", {
    policy,
    change_note: "Created from Admin UI",
  });
  currentGovernanceDraftId = created.id;
  governanceDraftJsonEl.value = JSON.stringify(created.policy, null, 2);
  await loadGovernance();
}

async function saveGovernanceDraft() {
  if (!currentGovernanceDraftId) {
    await createGovernanceDraftFromCurrent();
    return;
  }
  const policy = JSON.parse(governanceDraftJsonEl.value);
  const updated = await fetchJSONWithMethod(`/governance/policy/drafts/${currentGovernanceDraftId}`, "PATCH", {
    policy,
    change_note: "Updated from Admin UI",
  });
  governanceDraftJsonEl.value = JSON.stringify(updated.policy, null, 2);
  await loadGovernance();
}

async function validateGovernanceDraft() {
  if (!currentGovernanceDraftId) {
    throw new Error("No draft found. Create a draft first.");
  }
  await fetchJSONWithMethod(`/governance/policy/drafts/${currentGovernanceDraftId}/validate`, "POST");
}

async function publishGovernanceDraft() {
  if (!currentGovernanceDraftId) {
    throw new Error("No draft found. Create a draft first.");
  }
  await fetchJSONWithMethod(`/governance/policy/drafts/${currentGovernanceDraftId}/publish`, "POST", {
    change_note: "Published from Admin UI",
  });
  await loadGovernance();
}

async function loadNode(type, id) {
  hideError();
  const details = type === "artifact" ? { artifact: await fetchJSON(`/artifacts/${encodeURIComponent(id)}`) } : await fetchJSON(`/explorer/node/${type}/${id}`);
  nodeState.type = type;
  nodeState.id = id;
  renderDetail(details);
}

async function refreshAll() {
  if (!authReady) return;
  try {
    const token = currentToken();
    window.localStorage.setItem(TOKEN_KEY, token);
    await loadTree();
    if (nodeState.type && nodeState.id) {
      await loadNode(nodeState.type, nodeState.id);
    }
  } catch (error) {
    showError(error.message);
  }
}

function setActiveTab(tab) {
  if (tab === "onboarding") {
    explorerView.classList.add("hidden");
    onboardingView.classList.remove("hidden");
    adminView.classList.add("hidden");
    filtersBarEl.classList.add("hidden");
    summaryEl.classList.add("hidden");
    tabExplorer.classList.remove("active");
    tabOnboarding.classList.add("active");
    tabAdmin.classList.remove("active");
    loadOnboardingManifest().catch((error) => showError(error.message));
    return;
  }
  if (tab === "admin") {
    explorerView.classList.add("hidden");
    onboardingView.classList.add("hidden");
    adminView.classList.remove("hidden");
    filtersBarEl.classList.add("hidden");
    summaryEl.classList.add("hidden");
    tabExplorer.classList.remove("active");
    tabOnboarding.classList.remove("active");
    tabAdmin.classList.add("active");
    loadGovernance().catch((error) => showError(error.message));
    return;
  }
  adminView.classList.add("hidden");
  onboardingView.classList.add("hidden");
  explorerView.classList.remove("hidden");
  filtersBarEl.classList.remove("hidden");
  summaryEl.classList.remove("hidden");
  tabOnboarding.classList.remove("active");
  tabAdmin.classList.remove("active");
  tabExplorer.classList.add("active");
}

document.getElementById("refresh-btn").addEventListener("click", refreshAll);
document.getElementById("apply-btn").addEventListener("click", refreshAll);
tokenInput.addEventListener("change", refreshAll);
treeViewFilter.addEventListener("change", async () => {
  syncViewFilters();
  await refreshAll();
});
tabExplorer.addEventListener("click", () => setActiveTab("explorer"));
tabOnboarding.addEventListener("click", () => setActiveTab("onboarding"));
tabAdmin.addEventListener("click", () => setActiveTab("admin"));
logoutBtn.addEventListener("click", () => {
  window.localStorage.removeItem(TOKEN_KEY);
  setToken("");
  showLoginGate();
});
document.getElementById("refresh-manifest").addEventListener("click", async () => {
  try {
    await loadOnboardingManifest();
  } catch (error) {
    showError(error.message);
  }
});
document.getElementById("copy-manifest-url").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(`${currentOnboardingEndpoint()}/agent-onboarding.json`);
  } catch (error) {
    showError(`copy failed: ${error.message}`);
  }
});
document.getElementById("apply-onboarding-endpoint").addEventListener("click", async () => {
  try {
    await loadOnboardingManifest();
  } catch (error) {
    showError(error.message);
  }
});
onboardingEndpointInput.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  try {
    await loadOnboardingManifest();
  } catch (error) {
    showError(error.message);
  }
});
document.getElementById("refresh-governance").addEventListener("click", async () => {
  try {
    await loadGovernance();
  } catch (error) {
    showError(error.message);
  }
});
document.getElementById("create-governance-draft").addEventListener("click", async () => {
  try {
    await createGovernanceDraftFromCurrent();
  } catch (error) {
    showError(error.message);
  }
});
document.getElementById("save-governance-draft").addEventListener("click", async () => {
  try {
    await saveGovernanceDraft();
  } catch (error) {
    showError(error.message);
  }
});
document.getElementById("validate-governance-draft").addEventListener("click", async () => {
  try {
    await validateGovernanceDraft();
  } catch (error) {
    showError(error.message);
  }
});
document.getElementById("publish-governance-draft").addEventListener("click", async () => {
  try {
    await publishGovernanceDraft();
  } catch (error) {
    showError(error.message);
  }
});

function initTokenFromContext() {
  const query = new URLSearchParams(window.location.search);
  const fromQuery = query.get("token");
  const fromStorage = window.localStorage.getItem(TOKEN_KEY);
  setToken(fromQuery || fromStorage || "");
}

function initExplorerViewFromStorage() {
  const stored = window.localStorage.getItem(EXPLORER_VIEW_KEY);
  if (stored && ["tasks", "artifacts", "governance"].includes(stored)) {
    treeViewFilter.value = stored;
  }
  syncViewFilters();
}

async function loginAndLoad(token) {
  setToken(token);
  window.localStorage.setItem(TOKEN_KEY, token);
  showAppShell();
  loadCollapsedNodes();
  setActiveTab("explorer");
  await refreshAll();
}

loginSubmitBtn.addEventListener("click", async () => {
  try {
    hideLoginError();
    const token = loginTokenInput.value.trim();
    if (!token) {
      throw new Error("Token is required.");
    }
    await loginAndLoad(token);
  } catch (error) {
    showLoginError(error.message);
  }
});

loginTokenInput.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  loginSubmitBtn.click();
});

async function init() {
  initTokenFromContext();
  initExplorerViewFromStorage();
  initOnboardingEndpoint();
  const token = currentToken();
  if (!token) {
    showLoginGate();
    return;
  }
  await loginAndLoad(token);
}

init().catch((error) => {
  showLoginGate();
  showLoginError(error.message);
});
