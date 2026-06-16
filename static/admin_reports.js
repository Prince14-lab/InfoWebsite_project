const reportSearch = document.getElementById("reportSearch");
const statusFilter = document.getElementById("statusFilter");
const reportRows = document.querySelectorAll(".report-row");

function filterReports() {
  const searchValue = (reportSearch?.value || "").toLowerCase();
  const selectedStatus = statusFilter?.value || "all";

  reportRows.forEach((row) => {
    const reportId = (row.dataset.report || "").toLowerCase();
    const customer = (row.dataset.customer || "").toLowerCase();
    const issue = (row.dataset.issue || "").toLowerCase();
    const status = (row.dataset.status || "").toLowerCase();
    const matchesSearch =
      reportId.includes(searchValue) ||
      customer.includes(searchValue) ||
      issue.includes(searchValue);
    const matchesStatus = selectedStatus === "all" || status === selectedStatus;
    row.style.display = matchesSearch && matchesStatus ? "grid" : "none";
  });
}

reportSearch?.addEventListener("input", filterReports);
statusFilter?.addEventListener("change", filterReports);

const reportModal = document.getElementById("reportDetailsModal");
const proofWrap = document.getElementById("modalProofWrap");
const proofPhoto = document.getElementById("modalProofPhoto");
const reportResponseForm = document.getElementById("reportResponseForm");
const adminResponseInput = document.getElementById("adminResponseInput");

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value || "N/A";
  }
}

document.querySelectorAll(".report-view-btn").forEach((button) => {
  button.addEventListener("click", () => {
    setText("modalReportId", button.dataset.reportId);
    setText("modalReporter", button.dataset.reporter);
    setText("modalReporterType", button.dataset.reporterType);
    setText("modalIssueType", button.dataset.issueType);
    setText("modalReference", button.dataset.reference);
    setText("modalReportStatus", button.dataset.status);
    setText("modalReportCreated", button.dataset.created);
    setText("modalReportReviewed", button.dataset.reviewed);
    setText("modalReportReason", button.dataset.reason);
    setText("modalOwnerResponse", button.dataset.ownerResponse);
    setText("modalAdminResponse", button.dataset.adminResponse);
    if (adminResponseInput) {
      adminResponseInput.value = button.dataset.adminResponse === "No admin response recorded." ? "" : (button.dataset.adminResponse || "");
    }
    if (reportResponseForm) {
      if (button.dataset.source === "reports") {
        reportResponseForm.action = `/admin/reports/${button.dataset.dbId}/respond`;
        reportResponseForm.style.display = "block";
      } else {
        reportResponseForm.removeAttribute("action");
        reportResponseForm.style.display = "none";
      }
    }

    if (button.dataset.proof && proofPhoto && proofWrap) {
      proofPhoto.src = button.dataset.proof;
      proofWrap.classList.remove("hidden");
    } else {
      proofWrap?.classList.add("hidden");
      if (proofPhoto) {
        proofPhoto.removeAttribute("src");
      }
    }

    reportModal?.classList.remove("hidden");
  });
});

document.querySelectorAll("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    button.closest(".admin-modal")?.classList.add("hidden");
  });
});

reportModal?.addEventListener("click", (event) => {
  if (event.target === reportModal) {
    reportModal.classList.add("hidden");
  }
});
