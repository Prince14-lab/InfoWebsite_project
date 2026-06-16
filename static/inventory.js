const openPlantModal = document.getElementById("openPlantModal");
const closePlantModal = document.getElementById("closePlantModal");
const cancelPlantForm = document.getElementById("cancelPlantForm");
const plantModal = document.getElementById("plantModal");
const plantForm = document.getElementById("plantForm");
const modalTitle = document.getElementById("modalTitle");

const plantSearch = document.getElementById("plantSearch");
const categoryFilter = document.getElementById("categoryFilter");
const inventoryGrid = document.getElementById("inventoryGrid");

const plantIdInput = document.getElementById("plantId");
const plantNameInput = document.getElementById("plantName");
const plantCategoryInput = document.getElementById("plantCategory");
const plantPriceInput = document.getElementById("plantPrice");
const plantStockInput = document.getElementById("plantStock");
const plantDescriptionInput = document.getElementById("plantDescription");
const plantImageInput = document.getElementById("plantImage");
const plantSampleImagesInput = document.getElementById("plantSampleImages");
const existingSamplePhotoInput = document.getElementById("existingSamplePhoto");

const messageToggle = document.getElementById("messageToggle");
const messengerPanel = document.getElementById("messengerPanel");
const collapseMessenger = document.getElementById("collapseMessenger");
const closeMessenger = document.getElementById("closeMessenger");
const contacts = document.querySelectorAll(".contact");
const chatTitle = document.getElementById("chatTitle");

function openModal(title = "Add New Plant") {
  modalTitle.textContent = title;
  plantModal.classList.remove("hidden");
}

function closeModal() {
  plantModal.classList.add("hidden");
  plantForm.reset();
  plantIdInput.value = "";
  if (existingSamplePhotoInput) existingSamplePhotoInput.value = "";
  if (plantSampleImagesInput) plantSampleImagesInput.value = "";
  modalTitle.textContent = "Add New Plant";
  plantForm.action = "/add-plant";
}

openPlantModal.addEventListener("click", () => {
  plantForm.reset();
  plantIdInput.value = "";
  if (existingSamplePhotoInput) existingSamplePhotoInput.value = "";
  if (plantSampleImagesInput) plantSampleImagesInput.value = "";
  plantForm.action = "/add-plant";
  openModal("Add New Plant");
});

closePlantModal.addEventListener("click", closeModal);
cancelPlantForm.addEventListener("click", closeModal);

plantModal.addEventListener("click", (e) => {
  if (e.target === plantModal) {
    closeModal();
  }
});

inventoryGrid.addEventListener("click", function (e) {
  const card = e.target.closest(".inventory-card");
  if (!card) return;

  if (e.target.classList.contains("edit-btn")) {
    const plantId = card.dataset.id;
    const plantName = card.dataset.name;
    const plantCategory = card.dataset.category;
    const plantPrice = card.dataset.price;
    const plantStock = card.dataset.stock;
    const plantDescription = card.dataset.description || "";
    const plantImage = card.dataset.image;
    const samplePhoto = card.dataset.samplePhoto || "";
    const samplePhotos = card.dataset.samplePhotos || samplePhoto;

    plantIdInput.value = plantId;
    plantNameInput.value = card.querySelector("h3").textContent;
    plantCategoryInput.value = plantCategory;
    plantPriceInput.value = plantPrice;
    plantStockInput.value = plantStock;
    if (plantDescriptionInput) plantDescriptionInput.value = plantDescription;
    plantImageInput.value = plantImage;
    if (existingSamplePhotoInput) existingSamplePhotoInput.value = samplePhoto;
    if (plantSampleImagesInput) plantSampleImagesInput.value = samplePhotos;

    plantForm.action = `/edit-plant/${plantId}`;
    openModal("Edit Plant");
  }
});

function filterPlants() {
  const searchValue = plantSearch.value.toLowerCase();
  const selectedCategory = categoryFilter.value;
  const cards = document.querySelectorAll(".inventory-card");

  cards.forEach((card) => {
    const plantName = card.dataset.name;
    const plantCategory = card.dataset.category;

    const matchesSearch = plantName.includes(searchValue);
    const matchesCategory =
      selectedCategory === "all" || plantCategory === selectedCategory;

    card.style.display = matchesSearch && matchesCategory ? "block" : "none";
  });
}

plantSearch.addEventListener("input", filterPlants);
categoryFilter.addEventListener("change", filterPlants);

/* Messenger */
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
