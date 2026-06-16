const tabButtons = document.querySelectorAll(".tab-btn");
const tabContents = document.querySelectorAll(".tab-content");

function activateTab(tabId) {
  const button = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  const content = document.getElementById(tabId);

  if (!button || !content) return;

  tabButtons.forEach(btn => btn.classList.remove("active"));
  tabContents.forEach(item => item.classList.remove("active"));

  button.classList.add("active");
  content.classList.add("active");
}

tabButtons.forEach(button => {
  button.addEventListener("click", () => {
    activateTab(button.dataset.tab);
  });
});

const pageParams = new URLSearchParams(window.location.search);
const tabAliases = {
  to_pay: "toPay",
  to_ship: "toShip",
  to_receive: "toReceive",
  completed: "completed",
  return_refund: "returnRefund",
  cancelled: "cancelled"
};
const requestedTab = tabAliases[pageParams.get("tab")] || pageParams.get("tab");
if (requestedTab) {
  activateTab(requestedTab);
}

document.querySelectorAll(".return-toggle-btn").forEach(button => {
  button.addEventListener("click", () => {
    const wrapper = button.closest(".return-request-toggle");
    const form = wrapper?.querySelector(".return-form");
    if (!form) return;

    const isOpen = !form.classList.contains("hidden");
    form.classList.toggle("hidden", isOpen);
    button.setAttribute("aria-expanded", String(!isOpen));
    button.textContent = isOpen ? "Request Return / Refund" : "Hide Return / Refund";
  });
});

const messageToggle = document.getElementById("messageToggle");
const messengerPanel = document.getElementById("messengerPanel");
const collapseMessenger = document.getElementById("collapseMessenger");
const closeMessenger = document.getElementById("closeMessenger");
const contacts = document.querySelectorAll(".contact");
const chatTitle = document.getElementById("chatTitle");

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
