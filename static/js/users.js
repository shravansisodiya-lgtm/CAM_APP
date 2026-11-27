(function () {
  const modal = document.getElementById("user-modal");
  const modalTitle = document.getElementById("user-modal-title");
  const modalError = document.getElementById("user-modal-error");
  const idField = document.getElementById("user-id");
  const usernameField = document.getElementById("user-username");
  const passwordField = document.getElementById("user-password");
  const passwordFieldWrap = document.getElementById("user-password-field");
  const phoneField = document.getElementById("user-phone");
  const roleField = document.getElementById("user-role");
  const activeFieldWrap = document.getElementById("user-active-field");
  const activeField = document.getElementById("user-active");
  const branchFieldWrap = document.getElementById("user-branch-field");
  const allBranchesCb = document.getElementById("user-all-branches");
  const branchListWrap = document.getElementById("user-branch-list");
  const branchCbs = () => Array.from(document.querySelectorAll(".user-branch-cb"));

  function syncBranchUI() {
    // Admins always see everything, so the picker is hidden for them.
    branchFieldWrap.style.display = roleField.value === "admin" ? "none" : "";
    const all = allBranchesCb.checked;
    branchListWrap.style.opacity = all ? "0.45" : "1";
    branchCbs().forEach((cb) => { cb.disabled = all; });
  }

  function setBranches(list) {
    // list === null -> all branches
    allBranchesCb.checked = list === null;
    const set = new Set(list || []);
    branchCbs().forEach((cb) => { cb.checked = set.has(cb.value); });
    syncBranchUI();
  }

  allBranchesCb.addEventListener("change", syncBranchUI);
  roleField.addEventListener("change", syncBranchUI);

  const deleteModal = document.getElementById("delete-user-modal");
  const deleteText = document.getElementById("delete-user-modal-text");
  let pendingDeleteId = null;

  function openModal(mode, row) {
    modalError.textContent = "";
    if (mode === "add") {
      modalTitle.textContent = "Add user";
      idField.value = "";
      usernameField.value = "";
      passwordField.value = "";
      phoneField.value = "";
      roleField.value = "user";
      activeField.value = "1";
      setBranches(null);
      // New users need a starting password, so this field only appears
      // when adding.
      passwordFieldWrap.style.display = "";
      activeFieldWrap.style.display = "none";
    } else {
      modalTitle.textContent = "Edit user";
      idField.value = row.dataset.id;
      usernameField.value = row.dataset.username;
      phoneField.value = row.dataset.phone;
      roleField.value = row.dataset.role;
      activeField.value = row.dataset.active;
      let list = null;
      try { list = row.dataset.branches ? JSON.parse(row.dataset.branches) : null; } catch (e) { list = null; }
      setBranches(list);
      // Changing an existing user's password goes through the dedicated
      // "Reset password" button instead (it generates a strong random
      // password) - so there's exactly one way to change a password, not
      // two that behave differently.
      passwordFieldWrap.style.display = "none";
      activeFieldWrap.style.display = "";
    }
    modal.classList.add("open");
    usernameField.focus();
  }

  function closeModal() {
    modal.classList.remove("open");
  }

  document.getElementById("btn-add-user").addEventListener("click", () => openModal("add"));
  document.getElementById("btn-cancel-user-modal").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

  document.querySelectorAll(".btn-edit-user").forEach((btn) => {
    btn.addEventListener("click", () => openModal("edit", btn.closest("tr")));
  });

  document.getElementById("btn-save-user").addEventListener("click", async () => {
    const id = idField.value;
    const payload = {
      username: usernameField.value.trim(),
      phone_number: phoneField.value.trim(),
      role: roleField.value,
      all_branches: allBranchesCb.checked,
      branches: branchCbs().filter((cb) => cb.checked).map((cb) => cb.value),
    };
    if (!id) {
      payload.password = passwordField.value;
    } else {
      payload.is_active = activeField.value;
    }

    const url = id ? `/api/users/${id}` : "/api/users";
    const method = id ? "PUT" : "POST";

    try {
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        modalError.textContent = data.error || "Something went wrong.";
        return;
      }
      window.location.reload();
    } catch (e) {
      modalError.textContent = "Network error - is the server running?";
    }
  });

  document.querySelectorAll(".btn-delete-user").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest("tr");
      pendingDeleteId = row.dataset.id;
      deleteText.textContent = `Remove "${row.dataset.username}"? They will lose access immediately.`;
      deleteModal.classList.add("open");
    });
  });

  document.getElementById("btn-cancel-delete-user").addEventListener("click", () => {
    deleteModal.classList.remove("open");
    pendingDeleteId = null;
  });
  deleteModal.addEventListener("click", (e) => { if (e.target === deleteModal) deleteModal.classList.remove("open"); });

  document.getElementById("btn-confirm-delete-user").addEventListener("click", async () => {
    if (!pendingDeleteId) return;
    try {
      const res = await fetch(`/api/users/${pendingDeleteId}`, { method: "DELETE" });
      const data = await res.json();
      deleteModal.classList.remove("open");
      if (res.ok) {
        window.location.reload();
      } else {
        alert(data.error || "Could not delete that user.");
      }
    } catch (e) {
      deleteModal.classList.remove("open");
      alert("Network error - is the server running?");
    }
  });

  // ---------- reset password ----------

  const resetModal = document.getElementById("reset-password-modal");
  const generatedPasswordField = document.getElementById("generated-password");

  document.querySelectorAll(".btn-reset-user-password").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = btn.closest("tr");
      const userId = row.dataset.id;
      const confirmed = confirm(
        `Generate a new password for "${row.dataset.username}"? Their old password stops working immediately.`
      );
      if (!confirmed) return;

      try {
        const res = await fetch(`/api/users/${userId}/reset-password`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          alert(data.error || "Could not reset that password.");
          return;
        }
        generatedPasswordField.value = data.new_password;
        resetModal.classList.add("open");
      } catch (e) {
        alert("Network error - is the server running?");
      }
    });
  });

  document.getElementById("btn-copy-password").addEventListener("click", () => {
    generatedPasswordField.select();
    if (navigator.clipboard) {
      navigator.clipboard.writeText(generatedPasswordField.value).catch(() => {});
    }
  });

  document.getElementById("btn-close-reset-modal").addEventListener("click", () => {
    resetModal.classList.remove("open");
    window.location.reload();
  });
})();
