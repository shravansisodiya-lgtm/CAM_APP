(function () {
  const modal = document.getElementById("camera-modal");
  const modalTitle = document.getElementById("modal-title");
  const modalError = document.getElementById("modal-error");
  const idField = document.getElementById("camera-id");
  const nameField = document.getElementById("camera-name");
  const ipField = document.getElementById("camera-ip");
  const branchField = document.getElementById("camera-branch");
  const priorityField = document.getElementById("camera-priority");

  const deleteModal = document.getElementById("delete-modal");
  const deleteText = document.getElementById("delete-modal-text");
  let pendingDeleteId = null;

  function openModal(mode, row) {
    modalError.textContent = "";
    if (mode === "add") {
      modalTitle.textContent = "Add camera";
      idField.value = ""; nameField.value = ""; ipField.value = ""; branchField.value = "";
      priorityField.value = "medium";
    } else {
      modalTitle.textContent = "Edit camera";
      idField.value = row.dataset.id;
      nameField.value = row.dataset.name;
      ipField.value = row.dataset.ip;
      branchField.value = row.dataset.branch;
      priorityField.value = row.dataset.priority || "medium";
    }
    modal.classList.add("open");
    nameField.focus();
  }
  function closeModal() { modal.classList.remove("open"); }

  document.getElementById("btn-add-camera").addEventListener("click", () => openModal("add"));
  document.getElementById("btn-cancel-modal").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

  document.querySelectorAll(".btn-edit-camera").forEach((btn) => {
    btn.addEventListener("click", () => openModal("edit", btn.closest("tr")));
  });

  document.getElementById("btn-save-camera").addEventListener("click", async () => {
    const id = idField.value;
    const payload = {
      name: nameField.value.trim(),
      ip: ipField.value.trim(),
      branch: branchField.value.trim() || "Unassigned",
      priority: priorityField.value,
    };
    const url = id ? `/api/cameras/${id}` : "/api/cameras";
    const method = id ? "PUT" : "POST";
    try {
      const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const data = await res.json();
      if (!res.ok) { modalError.textContent = data.error || "Something went wrong."; return; }
      window.location.reload();
    } catch (e) { modalError.textContent = "Network error - is the server running?"; }
  });

  document.querySelectorAll(".btn-delete-camera").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest("tr");
      pendingDeleteId = row.dataset.id;
      deleteText.textContent = `Remove "${row.dataset.name}" (${row.dataset.ip})? Monitoring for it will stop immediately.`;
      deleteModal.classList.add("open");
    });
  });
  document.getElementById("btn-cancel-delete").addEventListener("click", () => {
    deleteModal.classList.remove("open");
    pendingDeleteId = null;
  });
  deleteModal.addEventListener("click", (e) => { if (e.target === deleteModal) deleteModal.classList.remove("open"); });
  document.getElementById("btn-confirm-delete").addEventListener("click", async () => {
    if (!pendingDeleteId) return;
    try {
      const res = await fetch(`/api/cameras/${pendingDeleteId}`, { method: "DELETE" });
      if (res.ok) window.location.reload();
      else deleteModal.classList.remove("open");
    } catch (e) { deleteModal.classList.remove("open"); }
  });

  // ---------- bulk import (pasted text + Excel upload) ----------
  const bulkModal = document.getElementById("bulk-modal");
  const bulkText = document.getElementById("bulk-text");
  const bulkFile = document.getElementById("bulk-file");
  const bulkError = document.getElementById("bulk-error");
  const bulkResult = document.getElementById("bulk-result");
  const btnBulkImport = document.getElementById("btn-bulk-import");

  function renderBulkResult(data) {
    let summary = `Added ${data.added} camera(s).`;
    if (data.skipped_existing && data.skipped_existing.length) {
      summary += ` Skipped ${data.skipped_existing.length} that already existed.`;
    }
    bulkResult.textContent = summary;
    bulkResult.dataset.imported = data.added > 0 ? "1" : "0";
    if (data.errors && data.errors.length) {
      bulkError.textContent = data.errors.slice(0, 5).join(" | ") + (data.errors.length > 5 ? ` (+${data.errors.length - 5} more)` : "");
    }
  }

  if (btnBulkImport && bulkModal) {
    btnBulkImport.addEventListener("click", () => {
      bulkText.value = "";
      if (bulkFile) bulkFile.value = "";
      bulkError.textContent = ""; bulkResult.textContent = ""; bulkResult.dataset.imported = "0";
      bulkModal.classList.add("open");
      bulkText.focus();
    });
    document.getElementById("btn-cancel-bulk").addEventListener("click", () => {
      bulkModal.classList.remove("open");
      if (bulkResult.dataset.imported === "1") window.location.reload();
    });
    bulkModal.addEventListener("click", (e) => { if (e.target === bulkModal) bulkModal.classList.remove("open"); });

    document.getElementById("btn-submit-bulk").addEventListener("click", async () => {
      const text = bulkText.value.trim();
      bulkError.textContent = ""; bulkResult.textContent = "";
      if (!text) { bulkError.textContent = "Paste at least one camera line first."; return; }
      const btn = document.getElementById("btn-submit-bulk");
      btn.disabled = true; btn.textContent = "Importing...";
      try {
        const res = await fetch("/api/cameras/bulk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
        const data = await res.json();
        if (!res.ok) bulkError.textContent = data.error || "Import failed.";
        else renderBulkResult(data);
      } catch (e) { bulkError.textContent = "Network error - is the server running?"; }
      btn.disabled = false; btn.textContent = "Import pasted text";
    });

    document.getElementById("btn-submit-bulk-excel").addEventListener("click", async () => {
      bulkError.textContent = ""; bulkResult.textContent = "";
      if (!bulkFile.files || bulkFile.files.length === 0) { bulkError.textContent = "Choose an .xlsx file first."; return; }
      const formData = new FormData();
      formData.append("file", bulkFile.files[0]);
      const btn = document.getElementById("btn-submit-bulk-excel");
      btn.disabled = true; btn.textContent = "Uploading...";
      try {
        const res = await fetch("/api/cameras/bulk-excel", { method: "POST", body: formData });
        const data = await res.json();
        if (!res.ok) bulkError.textContent = data.error || "Upload failed.";
        else renderBulkResult(data);
      } catch (e) { bulkError.textContent = "Network error - is the server running?"; }
      btn.disabled = false; btn.textContent = "Upload .xlsx";
    });
  }
})();
