(function () {
  const POLL_MS = 5000;
  let currentBranch = "All";
  let currentSearch = "";

  function formatElapsed(seconds) {
    if (seconds < 60) return "<1m offline";
    const m = Math.floor(seconds / 60);
    if (m < 60) return `${m}m offline`;
    const h = Math.floor(m / 60);
    const rem = m % 60;
    return `${h}h ${rem}m offline`;
  }

  function applyFilters() {
    const grid = document.getElementById("camera-grid");
    if (!grid) return;
    const tiles = grid.querySelectorAll(".camera-tile");
    let visibleCount = 0;
    tiles.forEach((tile) => {
      const matchesBranch = currentBranch === "All" || tile.dataset.branch === currentBranch;
      const matchesSearch = !currentSearch || tile.dataset.search.includes(currentSearch);
      const show = matchesBranch && matchesSearch;
      tile.style.display = show ? "" : "none";
      if (show) visibleCount++;
    });
    const emptyState = document.getElementById("no-results-state");
    if (emptyState) emptyState.style.display = visibleCount === 0 ? "block" : "none";
  }

  function updatePillCounts(branches) {
    const pillsWrap = document.getElementById("branch-pills");
    if (!pillsWrap || !branches) return;
    branches.forEach((b) => {
      const pill = pillsWrap.querySelector(`.pill[data-branch="${CSS.escape(b.branch)}"]`);
      if (pill) {
        const countEl = pill.querySelector(".pill-count");
        if (countEl) countEl.textContent = `${b.online}/${b.total}`;
      }
    });
  }

  function render(data) {
    document.getElementById("stat-total").textContent = data.total_count;
    document.getElementById("stat-online").textContent = data.online_count;
    document.getElementById("stat-offline").textContent = data.offline_count;
    const lastUpdate = document.getElementById("last-update");
    if (lastUpdate) lastUpdate.textContent = data.server_time;
    if (data.branches) updatePillCounts(data.branches);
    const grid = document.getElementById("camera-grid");
    if (!grid) return;
    data.cameras.forEach((cam) => {
      const tile = grid.querySelector(`.camera-tile[data-id="${cam.id}"]`);
      if (!tile) return;
      tile.classList.toggle("is-offline", !cam.is_online);
      const dot = tile.querySelector(".status-dot");
      dot.classList.toggle("online", !!cam.is_online);
      dot.classList.toggle("offline", !cam.is_online);
      const meta = tile.querySelector(".tile-meta");
      meta.classList.toggle("offline-since", !cam.is_online);
      meta.textContent = cam.is_online ? "online now" : "offline · " + formatElapsed(cam.offline_seconds || 0);
    });
  }

  async function poll() {
    try {
      const res = await fetch("/api/status", { headers: { "Accept": "application/json" } });
      if (res.status === 401) { window.location.href = "/login"; return; }
      const data = await res.json();
      render(data);
    } catch (e) { /* silent - next poll retries */ }
  }

  function setupFilters() {
    const pillsWrap = document.getElementById("branch-pills");
    if (pillsWrap) {
      pillsWrap.addEventListener("click", (e) => {
        const btn = e.target.closest(".pill");
        if (!btn) return;
        pillsWrap.querySelectorAll(".pill").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        currentBranch = btn.dataset.branch;
        applyFilters();
      });
    }
    const searchInput = document.getElementById("camera-search");
    if (searchInput) {
      searchInput.addEventListener("input", () => {
        currentSearch = searchInput.value.trim().toLowerCase();
        applyFilters();
      });
    }
  }

  setupFilters();
  poll();
  setInterval(poll, POLL_MS);
})();
