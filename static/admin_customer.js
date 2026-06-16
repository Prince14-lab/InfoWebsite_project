const customerSearch = document.getElementById("customerSearch");
const statusFilter = document.getElementById("statusFilter");
const customerRows = document.querySelectorAll(".customer-row");

function filterCustomers() {
  const searchValue = (customerSearch?.value || "").toLowerCase();
  const selectedStatus = statusFilter?.value || "all";

  customerRows.forEach((row) => {
    const name = (row.dataset.name || "").toLowerCase();
    const email = (row.dataset.email || "").toLowerCase();
    const status = (row.dataset.status || "").toLowerCase();
    const matchesSearch = name.includes(searchValue) || email.includes(searchValue);
    const matchesStatus = selectedStatus === "all" || status === selectedStatus;
    row.style.display = matchesSearch && matchesStatus ? "grid" : "none";
  });
}

customerSearch?.addEventListener("input", filterCustomers);
statusFilter?.addEventListener("change", filterCustomers);

document.querySelectorAll(".status-toggle-form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const action = form.dataset.action || "update";
    const confirmed = confirm(`Are you sure you want to ${action} this customer account?`);
    if (!confirmed) {
      event.preventDefault();
    }
  });
});

const customerModal = document.getElementById("customerDetailsModal");

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value || "N/A";
  }
}

document.querySelectorAll(".customer-view-btn").forEach((button) => {
  button.addEventListener("click", () => {
    setText("modalCustomerName", button.dataset.fullname);
    setText("modalCustomerEmail", button.dataset.email);
    setText("modalCustomerUsername", button.dataset.username);
    setText("modalCustomerPhone", button.dataset.phone);
    setText("modalCustomerAddress", button.dataset.address);
    setText("modalCustomerCreated", button.dataset.created);
    setText("modalCustomerStatus", button.dataset.status);
    setText("modalCustomerOrders", button.dataset.orders);
    setText("modalCustomerRecentOrders", button.dataset.recentOrders);
    customerModal?.classList.remove("hidden");
  });
});

document.querySelectorAll("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    button.closest(".admin-modal")?.classList.add("hidden");
  });
});

customerModal?.addEventListener("click", (event) => {
  if (event.target === customerModal) {
    customerModal.classList.add("hidden");
  }
});
