const state = {
  data: window.SCIENCE_WEBSITE_DATA || null,
  activeTab: "dashboard",
  graphFilter: "",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function text(value, fallback = "") {
  if (value === null || value === undefined) return fallback;
  return String(value);
}

function escapeHtml(value) {
  return text(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, Number(value) || 0));
}

function emptyState(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function setTab(tabName) {
  state.activeTab = tabName;
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === tabName));
  $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === tabName));
  if (tabName === "graph") {
    renderGraph();
  }
}

function initTabs() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
  });
}

async function loadData() {
  if (state.data) return state.data;
  try {
    const response = await fetch("website_data.json");
    state.data = await response.json();
  } catch (error) {
    state.data = {
      meta: { title: "No exported data", domain: "Run export_science_website_data.py first." },
      stats: { totalPapers: 0, totalSubspaces: 0, totalGaps: 0, totalTriples: 0 },
      subspaces: [],
      papers: [],
      triples: [],
      gaps: [],
      graph: { nodes: [], links: [] },
    };
  }
  return state.data;
}

function renderAll() {
  renderHeader();
  renderDashboard();
  renderSubspaces();
  renderGapFilters();
  renderGaps();
  renderPaperFilters();
  renderPapers();
  renderGraph();
}

function renderHeader() {
  const { meta, stats } = state.data;
  $("#projectTitle").textContent = meta.title || "Science Research Project";
  $("#projectDomain").textContent = meta.domain || "";
  $("#dataStatus").textContent = `${stats.totalPapers || 0} papers, ${stats.totalGaps || 0} gaps`;
}

function renderDashboard() {
  const { meta, stats, subspaces } = state.data;
  $("#paperCount").textContent = stats.totalPapers || 0;
  $("#subspaceCount").textContent = stats.totalSubspaces || 0;
  $("#gapCount").textContent = stats.totalGaps || 0;
  $("#tripleCount").textContent = stats.totalTriples || 0;
  $("#projectObjective").textContent = meta.objective || "No objective recorded.";
  $("#projectPhase").textContent = meta.phase || "Unknown";
  $("#projectSource").textContent = meta.sourceProject || "";
  $("#projectUpdated").textContent = meta.updatedAt ? `Updated ${meta.updatedAt}` : "";

  const rows = subspaces
    .slice()
    .sort((a, b) => (Number(b.importance) || 0) - (Number(a.importance) || 0))
    .slice(0, 10);
  if (!rows.length) {
    $("#subspaceBars").innerHTML = emptyState("No subspace scan exported yet.");
    return;
  }
  const maxHits = Math.max(1, ...rows.map((row) => Number(row.hitCount) || 0));
  $("#subspaceBars").innerHTML = rows
    .map((row) => {
      const hitWidth = Math.round(((Number(row.hitCount) || 0) / maxHits) * 100);
      const importance = clamp(row.importance, 0, 10);
      return `
        <div class="bar-row" title="${escapeHtml(row.name)}">
          <div class="bar-label">${escapeHtml(row.name)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${hitWidth}%"></div></div>
          <div class="score-pill">${importance}/10</div>
        </div>
      `;
    })
    .join("");
}

function renderSubspaces() {
  const subspaces = state.data.subspaces || [];
  if (!subspaces.length) {
    $("#subspaceGrid").innerHTML = emptyState("No domain subspaces found.");
    return;
  }
  $("#subspaceGrid").innerHTML = subspaces
    .map(
      (item) => `
      <article class="subspace-card ${item.selected ? "selected" : ""}" data-subspace-id="${escapeHtml(item.id)}">
        <div class="card-title">
          <h3>${escapeHtml(item.name)}</h3>
          <span class="score-pill">${escapeHtml(item.importance)}/10</span>
        </div>
        <p class="item-body">${escapeHtml(item.description || "No description.")}</p>
        <div class="keyword-row">
          ${(item.keywords || []).slice(0, 6).map((keyword) => `<span class="tag">${escapeHtml(keyword)}</span>`).join("")}
        </div>
        <div class="item-meta">
          <span>${escapeHtml(item.hitCount)} probe hits</span>
          <span>${escapeHtml(item.recentHitCount)} recent</span>
          <span>${escapeHtml(item.density)}</span>
          <span>${escapeHtml(item.strategy)}</span>
        </div>
      </article>
    `
    )
    .join("");

  $$(".subspace-card").forEach((card) => {
    card.addEventListener("click", () => card.classList.toggle("selected"));
  });
}

function graphColor(kind) {
  if (kind === "method") return "#2563eb";
  if (kind === "scenario") return "#0f766e";
  if (kind === "benchmark") return "#b45309";
  return "#64748b";
}

function filteredGraph() {
  const graph = state.data.graph || { nodes: [], links: [] };
  const query = state.graphFilter.trim().toLowerCase();
  if (!query) return graph;
  const keep = new Set();
  graph.nodes.forEach((node) => {
    if (text(node.label).toLowerCase().includes(query) || text(node.kind).toLowerCase().includes(query)) {
      keep.add(node.id);
    }
  });
  graph.links.forEach((link) => {
    const source = typeof link.source === "string" ? link.source : link.source.id;
    const target = typeof link.target === "string" ? link.target : link.target.id;
    if (keep.has(source) || keep.has(target) || text(link.kind).toLowerCase().includes(query)) {
      keep.add(source);
      keep.add(target);
    }
  });
  return {
    nodes: graph.nodes.filter((node) => keep.has(node.id)),
    links: graph.links.filter((link) => keep.has(link.source) && keep.has(link.target)),
  };
}

function renderGraph() {
  const container = $("#graphCanvas");
  if (!container) return;
  const graph = filteredGraph();
  if (!graph.nodes.length) {
    container.innerHTML = emptyState("No graph nodes match the current filter.");
    return;
  }

  const width = Math.max(720, container.clientWidth || 720);
  const height = 560;
  const centerX = width / 2;
  const centerY = height / 2;
  const radius = Math.min(width, height) * 0.36;
  const nodes = graph.nodes.map((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(1, graph.nodes.length) - Math.PI / 2;
    return {
      ...node,
      x: centerX + Math.cos(angle) * radius,
      y: centerY + Math.sin(angle) * radius,
    };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const links = graph.links
    .map((link) => ({ ...link, sourceNode: byId.get(link.source), targetNode: byId.get(link.target) }))
    .filter((link) => link.sourceNode && link.targetNode);

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Knowledge graph">
      <g class="links">
        ${links
          .map(
            (link, index) => `
          <line class="link" data-link-index="${index}"
            x1="${link.sourceNode.x}" y1="${link.sourceNode.y}"
            x2="${link.targetNode.x}" y2="${link.targetNode.y}"
            stroke-width="${clamp(link.weight, 1, 5)}" />
        `
          )
          .join("")}
      </g>
      <g class="nodes">
        ${nodes
          .map(
            (node) => `
          <g class="node" data-node-id="${escapeHtml(node.id)}" transform="translate(${node.x},${node.y})">
            <circle r="${node.kind === "scenario" ? 13 : 11}" fill="${graphColor(node.kind)}"></circle>
            <text x="16" y="4">${escapeHtml(shortLabel(node.label, 34))}</text>
          </g>
        `
          )
          .join("")}
      </g>
    </svg>
  `;

  $$(".node").forEach((nodeEl) => {
    nodeEl.addEventListener("click", () => showNodeDetails(nodeEl.dataset.nodeId));
  });
  $$(".link").forEach((linkEl) => {
    linkEl.addEventListener("click", () => showLinkDetails(links[Number(linkEl.dataset.linkIndex)]));
  });
}

function shortLabel(label, limit) {
  const value = text(label);
  if (value.length <= limit) return value;
  return value.slice(0, limit - 3) + "...";
}

function showNodeDetails(nodeId) {
  const graph = state.data.graph || { nodes: [], links: [] };
  const node = graph.nodes.find((item) => item.id === nodeId);
  if (!node) return;
  const links = graph.links.filter((link) => link.source === nodeId || link.target === nodeId);
  $("#graphDetails").innerHTML = `
    <h3>${escapeHtml(node.label)}</h3>
    <div class="item-meta">
      <span>${escapeHtml(node.kind)}</span>
      <span>${links.length} links</span>
    </div>
    <div class="list-stack">
      ${links
        .map((link) => {
          const otherId = link.source === nodeId ? link.target : link.source;
          const other = graph.nodes.find((item) => item.id === otherId);
          return `<p class="item-body">${escapeHtml(link.kind)}: ${escapeHtml(other ? other.label : otherId)}</p>`;
        })
        .join("")}
    </div>
  `;
}

function showLinkDetails(link) {
  if (!link) return;
  $("#graphDetails").innerHTML = `
    <h3>${escapeHtml(link.kind)}</h3>
    <div class="item-meta">
      <span>weight ${escapeHtml(link.weight)}</span>
    </div>
    <p class="item-body">${escapeHtml(link.sourceNode.label)} -> ${escapeHtml(link.targetNode.label)}</p>
  `;
}

function renderGapFilters() {
  const types = Array.from(new Set((state.data.gaps || []).map((gap) => gap.type).filter(Boolean))).sort();
  $("#gapTypeFilter").innerHTML =
    `<option value="all">All types</option>` +
    types.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`).join("");
}

function renderGaps() {
  const type = $("#gapTypeFilter").value || "all";
  const sort = $("#gapSort").value || "rank";
  let gaps = (state.data.gaps || []).slice();
  if (type !== "all") gaps = gaps.filter((gap) => gap.type === type);
  if (sort === "novelty") {
    gaps.sort((a, b) => (Number(b.novelty) || 0) - (Number(a.novelty) || 0));
  } else if (sort === "feasibility") {
    const rank = { high: 3, medium: 2, low: 1 };
    gaps.sort((a, b) => (rank[b.feasibility] || 0) - (rank[a.feasibility] || 0));
  } else {
    gaps.sort((a, b) => (Number(a.rank) || 0) - (Number(b.rank) || 0));
  }
  if (!gaps.length) {
    $("#gapList").innerHTML = emptyState("No gaps match the current filters.");
    return;
  }
  $("#gapList").innerHTML = gaps
    .map(
      (gap) => `
      <article class="list-item">
        <h3>#${escapeHtml(gap.rank)} ${escapeHtml(gap.type)}</h3>
        <p class="item-body">${escapeHtml(gap.description)}</p>
        <div class="item-meta">
          <span>novelty ${escapeHtml(gap.novelty || "n/a")}</span>
          <span>application ${escapeHtml(gap.application || "n/a")}</span>
          <span>feasibility ${escapeHtml(gap.feasibility || "n/a")}</span>
          ${gap.requiresHumanReview ? '<span class="tag warn">human review</span>' : ""}
        </div>
        <div class="item-actions">
          <button class="text-button" data-gap-id="${escapeHtml(gap.id)}">View details</button>
        </div>
      </article>
    `
    )
    .join("");
  $$("[data-gap-id]").forEach((button) => {
    button.addEventListener("click", () => showGapModal(button.dataset.gapId));
  });
}

function renderPaperFilters() {
  const years = Array.from(new Set((state.data.papers || []).map((paper) => paper.year).filter(Boolean))).sort().reverse();
  $("#paperYearFilter").innerHTML =
    `<option value="all">All years</option>` +
    years.map((year) => `<option value="${escapeHtml(year)}">${escapeHtml(year)}</option>`).join("");
}

function renderPapers() {
  const query = ($("#paperSearch").value || "").toLowerCase();
  const year = $("#paperYearFilter").value || "all";
  let papers = (state.data.papers || []).slice();
  if (year !== "all") papers = papers.filter((paper) => paper.year === year);
  if (query) {
    papers = papers.filter((paper) =>
      [paper.title, paper.venue, paper.method, paper.scenario, paper.benchmark, paper.abstract]
        .map((item) => text(item).toLowerCase())
        .some((value) => value.includes(query))
    );
  }
  if (!papers.length) {
    $("#paperList").innerHTML = emptyState("No papers match the current filters.");
    return;
  }
  $("#paperList").innerHTML = papers
    .map(
      (paper) => `
      <article class="list-item">
        <h3>${escapeHtml(paper.title)}</h3>
        <div class="item-meta">
          <span>${escapeHtml(paper.year || "year n/a")}</span>
          <span>${escapeHtml(paper.venue || "venue n/a")}</span>
          <span>credibility ${escapeHtml(paper.credibility || "n/a")}</span>
        </div>
        <div class="paper-tags">
          <span class="tag method">${escapeHtml(paper.method || "unknown method")}</span>
          <span class="tag scenario">${escapeHtml(paper.scenario || "unknown scenario")}</span>
          <span class="tag benchmark">${escapeHtml(paper.benchmark || "unknown benchmark")}</span>
        </div>
        <p class="item-body">${escapeHtml(paper.abstract || "No abstract exported.")}</p>
        <div class="item-actions">
          <button class="text-button" data-paper-id="${escapeHtml(paper.id)}">View details</button>
        </div>
      </article>
    `
    )
    .join("");
  $$("[data-paper-id]").forEach((button) => {
    button.addEventListener("click", () => showPaperModal(button.dataset.paperId));
  });
}

function showGapModal(gapId) {
  const gap = (state.data.gaps || []).find((item) => item.id === gapId);
  if (!gap) return;
  showModal(`
    <h2>${escapeHtml(gap.type)} gap</h2>
    <p class="item-body">${escapeHtml(gap.description)}</p>
    <div class="item-meta">
      <span>novelty ${escapeHtml(gap.novelty || "n/a")}</span>
      <span>application ${escapeHtml(gap.application || "n/a")}</span>
      <span>feasibility ${escapeHtml(gap.feasibility || "n/a")}</span>
      <span>overlap ${escapeHtml(gap.overlapRisk || "n/a")}</span>
    </div>
    <h3>Suggested path</h3>
    <p class="item-body">${escapeHtml(gap.recommendedApproach || "No recommendation recorded.")}</p>
    <h3>Value argument</h3>
    <p class="item-body">${escapeHtml(gap.valueArgument || "No value argument recorded.")}</p>
    <h3>Supporting references</h3>
    ${referenceList(gap.supportingReferences)}
  `);
}

function showPaperModal(paperId) {
  const paper = (state.data.papers || []).find((item) => item.id === paperId);
  if (!paper) return;
  showModal(`
    <h2>${escapeHtml(paper.title)}</h2>
    <div class="item-meta">
      <span>${escapeHtml(paper.year || "year n/a")}</span>
      <span>${escapeHtml(paper.venue || "venue n/a")}</span>
      <span>${escapeHtml(paper.provider || "provider n/a")}</span>
      <span>credibility ${escapeHtml(paper.credibility || "n/a")}</span>
    </div>
    <div class="paper-tags">
      <span class="tag method">${escapeHtml(paper.method || "unknown method")}</span>
      <span class="tag scenario">${escapeHtml(paper.scenario || "unknown scenario")}</span>
      <span class="tag benchmark">${escapeHtml(paper.benchmark || "unknown benchmark")}</span>
    </div>
    <h3>Abstract</h3>
    <p class="item-body">${escapeHtml(paper.abstract || "No abstract exported.")}</p>
    <h3>Contribution</h3>
    <p class="item-body">${escapeHtml(paper.contribution || "No contribution extracted.")}</p>
    <h3>Limitation</h3>
    <p class="item-body">${escapeHtml(paper.limitation || "No limitation extracted.")}</p>
    ${paper.url ? `<p class="item-body"><a href="${escapeHtml(paper.url)}" target="_blank" rel="noreferrer">Open source page</a></p>` : ""}
  `);
}

function referenceList(references) {
  const refs = references || [];
  if (!refs.length) return emptyState("No supporting references recorded.");
  return `<ul>${refs.map((ref) => `<li>${escapeHtml(ref)}</li>`).join("")}</ul>`;
}

function showModal(html) {
  $("#modalContent").innerHTML = html;
  $("#modal").classList.add("open");
  $("#modal").setAttribute("aria-hidden", "false");
}

function closeModal() {
  $("#modal").classList.remove("open");
  $("#modal").setAttribute("aria-hidden", "true");
}

function exportGapReport() {
  const report = {
    title: `${state.data.meta.title || "Science project"} gap report`,
    exportedAt: new Date().toISOString(),
    domain: state.data.meta.domain,
    gaps: state.data.gaps || [],
  };
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "gap_report.json";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function bindControls() {
  $("#gapTypeFilter").addEventListener("change", renderGaps);
  $("#gapSort").addEventListener("change", renderGaps);
  $("#paperSearch").addEventListener("input", renderPapers);
  $("#paperYearFilter").addEventListener("change", renderPapers);
  $("#graphSearch").addEventListener("input", (event) => {
    state.graphFilter = event.target.value;
    renderGraph();
  });
  $("#resetGraph").addEventListener("click", () => {
    state.graphFilter = "";
    $("#graphSearch").value = "";
    renderGraph();
  });
  $("#exportGapReport").addEventListener("click", exportGapReport);
  $$("[data-close-modal]").forEach((item) => item.addEventListener("click", closeModal));
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });
  window.addEventListener("resize", () => {
    if (state.activeTab === "graph") renderGraph();
  });
}

async function main() {
  initTabs();
  await loadData();
  bindControls();
  renderAll();
}

main();
