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




const buttons = {
  showOwnerInfo: document.getElementById("showOwnerInfo"),
  showShopInfo: document.getElementById("showShopInfo"),
  showPasswordSection: document.getElementById("showPasswordSection")
};

const sections = {
  ownerInfoSection: document.getElementById("ownerInfoSection"),
  shopInfoSection: document.getElementById("shopInfoSection"),
  passwordSection: document.getElementById("passwordSection")
};

function showSection(sectionId, buttonId) {
  Object.values(sections).forEach(section => {
    section?.classList.remove("active-section");
    section?.classList.add("hidden-section");
  });

  Object.values(buttons).forEach(button => {
    button?.classList.remove("active-tab");
  });

  sections[sectionId]?.classList.remove("hidden-section");
  sections[sectionId]?.classList.add("active-section");
  buttons[buttonId]?.classList.add("active-tab");
}

buttons.showOwnerInfo?.addEventListener("click", () => {
  showSection("ownerInfoSection", "showOwnerInfo");
});

buttons.showShopInfo?.addEventListener("click", () => {
  showSection("shopInfoSection", "showShopInfo");
});

buttons.showPasswordSection?.addEventListener("click", () => {
  showSection("passwordSection", "showPasswordSection");
});

showSection("ownerInfoSection", "showOwnerInfo");

function submitOwnerProfile(form) {
  const formData = new FormData(form);
  fetch("/update-owner-profile", {
    method: "POST",
    body: formData
  })
    .then(response => response.json())
    .then(data => {
      alert(data.message);
      if (data.success) {
        location.reload();
      }
    });
}

function submitShopInfo(form) {
  const formData = new FormData(form);
  fetch("/update-shop-info", {
    method: "POST",
    body: formData
  })
    .then(response => response.json())
    .then(data => {
      alert(data.message);
      if (data.success) {
        location.reload();
      }
    })
    .catch(() => {
      alert("Unable to save shop information right now.");
    });
}

const ownerForm = document.getElementById("ownerForm");
ownerForm?.addEventListener("submit", event => {
  event.preventDefault();
  submitOwnerProfile(ownerForm);
});

const shopInfoForm = document.getElementById("shopInfoForm");
shopInfoForm?.addEventListener("submit", event => {
  event.preventDefault();
  submitShopInfo(shopInfoForm);
});
