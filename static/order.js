const searchInput = document.getElementById("orderSearch");
const statusFilter = document.getElementById("statusFilter");
const orderCards = document.querySelectorAll(".order-card");
const messageToggle = document.getElementById("messageToggle");
const messengerPanel = document.getElementById("messengerPanel");
const collapseMessenger = document.getElementById("collapseMessenger");
const closeMessenger = document.getElementById("closeMessenger");
const contacts = document.querySelectorAll(".contact");
const chatTitle = document.getElementById("chatTitle");


function filterOrders() {
  const searchValue = searchInput.value.toLowerCase();
  const selectedStatus = statusFilter.value;

  orderCards.forEach((card) => {
    const customer = (card.dataset.customer || "").toLowerCase();
    const orderId = (card.dataset.order || "").toLowerCase();
    const status = (card.dataset.status || "").toLowerCase();

    const matchesSearch =
      customer.includes(searchValue) || orderId.includes(searchValue);

    const matchesStatus =
      selectedStatus === "all" || status === selectedStatus;

    card.style.display = matchesSearch && matchesStatus ? "block" : "none";
  });
}

searchInput.addEventListener("input", filterOrders);
statusFilter.addEventListener("change", filterOrders);




messageToggle.addEventListener("click", () => {
  messengerPanel.classList.remove("hidden");
  messengerPanel.classList.add("expanded");
  messengerPanel.classList.remove("collapsed");
  messageToggle.style.display = "none";
});

collapseMessenger.addEventListener("click", () => {
  if (messengerPanel.classList.contains("expanded")) {
    messengerPanel.classList.remove("expanded");
    messengerPanel.classList.add("collapsed");
  } else {
    messengerPanel.classList.remove("collapsed");
    messengerPanel.classList.add("expanded");
  }
});

closeMessenger.addEventListener("click", () => {
  messengerPanel.classList.add("hidden");
  messengerPanel.classList.remove("collapsed");
  messengerPanel.classList.remove("expanded");
  messageToggle.style.display = "inline-block";
});

contacts.forEach(contact => {
  contact.addEventListener("click", () => {
    contacts.forEach(c => c.classList.remove("active"));
    contact.classList.add("active");
    chatTitle.textContent = contact.dataset.name;
  });
});
