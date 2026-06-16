const searchInput = document.getElementById("searchInput");
const categoryButtons = document.querySelectorAll(".category-btn");
const plantCards = document.querySelectorAll(".plant-card-link");

let selectedCategory = "all";

function filterPlants() {
  const searchValue = searchInput.value.toLowerCase();

  plantCards.forEach(cardLink => {

    const card = cardLink.querySelector(".plant-card");

    const plantName = card.dataset.name.toLowerCase();
    const plantCategory = card.dataset.category.toLowerCase();

    const matchesSearch = plantName.includes(searchValue);

    const matchesCategory =
      selectedCategory === "all" ||
      plantCategory === selectedCategory;

    if (matchesSearch && matchesCategory) {
      cardLink.classList.remove("hidden");
    } else {
      cardLink.classList.add("hidden");
    }

  });
}

categoryButtons.forEach(button => {
  button.addEventListener("click", () => {
    categoryButtons.forEach(btn => btn.classList.remove("active"));
    button.classList.add("active");
    selectedCategory = button.dataset.category;
    filterPlants();
  });
});

searchInput.addEventListener("input", filterPlants);


const messageToggle = document.getElementById("messageToggle");
const messengerPanel = document.getElementById("messengerPanel");
const collapseMessenger = document.getElementById("collapseMessenger");
const closeMessenger = document.getElementById("closeMessenger");
const chatTitle = document.getElementById("chatTitle");
const chatMessages = document.getElementById("chatMessages");
const chatbotForm = document.getElementById("chatbotForm");
const chatbotInput = document.getElementById("chatbotInput");
const quickReplies = document.getElementById("quickReplies");

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

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value || "";
  return div.innerHTML;
}

function appendChatbotMessage(text, type) {
  if (!chatMessages) return;
  const message = document.createElement("div");
  message.className = `message ${type}`;
  message.innerHTML = escapeHtml(text);
  chatMessages.appendChild(message);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function sendChatbotMessage(text) {
  const message = (text || "").trim();
  if (!message) return;

  appendChatbotMessage(message, "sent");
  try {
    const response = await fetch("/customer-chatbot", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message }),
    });
    const data = await response.json();
    appendChatbotMessage(data.reply || "I am here to help, but I could not understand that yet.", "received");
  } catch (error) {
    appendChatbotMessage("Sorry, PlantPal is having trouble replying right now. Please try again in a moment.", "received");
  }
}

if (chatbotForm) {
  chatbotForm.addEventListener("submit", event => {
    event.preventDefault();
    const message = chatbotInput.value;
    chatbotInput.value = "";
    sendChatbotMessage(message);
  });
}

if (chatbotInput) {
  chatbotInput.addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      chatbotForm?.requestSubmit();
    }
  });
}

if (quickReplies && !document.querySelector(".message-widget[data-message-role]")) {
  quickReplies.querySelectorAll(".quick-reply-btn").forEach(button => {
    button.addEventListener("click", () => {
      sendChatbotMessage(button.textContent);
    });
  });
}
