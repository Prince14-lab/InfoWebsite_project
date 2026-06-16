const orderSearch = document.getElementById("orderSearch");
const statusFilter = document.getElementById("statusFilter");
const orderRows = document.querySelectorAll(".order-row");

function filterOrders() {
  const searchValue = (orderSearch?.value || "").toLowerCase();
  const selectedStatus = statusFilter?.value || "all";

  orderRows.forEach((row) => {
    const orderId = (row.dataset.order || "").toLowerCase();
    const customer = (row.dataset.customer || "").toLowerCase();
    const status = (row.dataset.status || "").toLowerCase();
    const matchesSearch = orderId.includes(searchValue) || customer.includes(searchValue);
    const matchesStatus = selectedStatus === "all" || status === selectedStatus;
    row.style.display = matchesSearch && matchesStatus ? "grid" : "none";
  });
}

orderSearch?.addEventListener("input", filterOrders);
statusFilter?.addEventListener("change", filterOrders);

const orderModal = document.getElementById("orderDetailsModal");

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value || "N/A";
  }
}

document.querySelectorAll(".order-view-btn").forEach((button) => {
  button.addEventListener("click", () => {
    setText("modalOrderCode", button.dataset.orderCode);
    setText("modalOrderCustomer", button.dataset.customer);
    setText("modalOrderContact", button.dataset.contact);
    setText("modalOrderAddress", button.dataset.address);
    setText("modalOrderPaymentMethod", button.dataset.paymentMethod);
    setText("modalOrderPaymentStatus", button.dataset.paymentStatus);
    setText("modalOrderStatus", button.dataset.orderStatus);
    setText("modalOrderSubtotal", button.dataset.subtotal);
    setText("modalOrderDeliveryFee", button.dataset.deliveryFee);
    setText("modalOrderTotal", button.dataset.total);
    setText("modalOrderDate", button.dataset.orderedAt);
    setText("modalOrderItems", button.dataset.items);
    orderModal?.classList.remove("hidden");
  });
});

document.querySelectorAll("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    button.closest(".admin-modal")?.classList.add("hidden");
  });
});

orderModal?.addEventListener("click", (event) => {
  if (event.target === orderModal) {
    orderModal.classList.add("hidden");
  }
});
