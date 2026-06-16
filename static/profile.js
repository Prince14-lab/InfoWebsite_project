const messageToggle = document.getElementById("messageToggle");
const messengerPanel = document.getElementById("messengerPanel");
const collapseMessenger = document.getElementById("collapseMessenger");
const closeMessenger = document.getElementById("closeMessenger");
const contacts = document.querySelectorAll(".contact");
const chatTitle = document.getElementById("chatTitle");
const reportToggle = document.getElementById("reportToggle");
const reportPanel = document.getElementById("reportPanel");
const closeReportPanel = document.getElementById("closeReportPanel");

if (messageToggle && messengerPanel) {
  messageToggle.addEventListener("click", () => {
    reportPanel?.classList.add("hidden");
    if (reportToggle) {
      reportToggle.style.display = "none";
    }
    messengerPanel.classList.remove("hidden");
    messengerPanel.classList.add("expanded");
    messengerPanel.classList.remove("collapsed");
    messageToggle.style.display = "none";
  });
}

if (collapseMessenger && messengerPanel && messageToggle) {
  collapseMessenger.addEventListener("click", () => {
    if (messengerPanel.classList.contains("expanded")) {
      messengerPanel.classList.remove("expanded");
      messengerPanel.classList.add("collapsed");
    } else {
      messengerPanel.classList.remove("collapsed");
      messengerPanel.classList.add("expanded");
    }
  });
}

if (closeMessenger && messengerPanel && messageToggle) {
  closeMessenger.addEventListener("click", () => {
    messengerPanel.classList.add("hidden");
    messengerPanel.classList.remove("collapsed");
    messengerPanel.classList.remove("expanded");
    messageToggle.style.display = "inline-block";
    if (reportToggle) {
      reportToggle.style.display = "inline-flex";
    }
  });
}

contacts.forEach(contact => {
  contact.addEventListener("click", () => {
    contacts.forEach(item => item.classList.remove("active"));
    contact.classList.add("active");

    if (chatTitle) {
      chatTitle.textContent = contact.dataset.name;
    }
  });
});

const buttons = {
  showCustomerInfo: document.getElementById("showCustomerInfo"),
  showDeliverySection: document.getElementById("showDeliverySection"),
  showPasswordSection: document.getElementById("showPasswordSection")
};

const sections = {
  customerInfoSection: document.getElementById("customerInfoSection"),
  deliverySection: document.getElementById("deliverySection"),
  passwordSection: document.getElementById("passwordSection")
};

function showSection(sectionId, buttonId) {
  Object.values(sections).forEach(section => {
    if (section) {
      section.classList.remove("active-section");
      section.classList.add("hidden-section");
    }
  });

  Object.values(buttons).forEach(button => {
    if (button) {
      button.classList.remove("active-tab");
    }
  });

  sections[sectionId]?.classList.remove("hidden-section");
  sections[sectionId]?.classList.add("active-section");
  buttons[buttonId]?.classList.add("active-tab");
}

buttons.showCustomerInfo?.addEventListener("click", () => {
  showSection("customerInfoSection", "showCustomerInfo");
});

buttons.showDeliverySection?.addEventListener("click", () => {
  showSection("deliverySection", "showDeliverySection");
});

buttons.showPasswordSection?.addEventListener("click", () => {
  showSection("passwordSection", "showPasswordSection");
});

showSection("customerInfoSection", "showCustomerInfo");

reportToggle?.addEventListener("click", event => {
  event.stopImmediatePropagation();
  messengerPanel?.classList.add("hidden");
  if (messageToggle) {
    messageToggle.style.display = "inline-block";
  }
  reportPanel?.classList.remove("hidden");
  reportToggle.style.display = "none";
}, true);

closeReportPanel?.addEventListener("click", event => {
  event.preventDefault();
  event.stopImmediatePropagation();
  if (!reportPanel || !reportToggle) return;
  reportPanel.classList.add("hidden");
  reportToggle.style.display = "inline-flex";
}, true);

const reportChatMessages = document.getElementById("reportChatMessages");
if (reportChatMessages) {
  reportChatMessages.scrollTop = reportChatMessages.scrollHeight;
}

function submitProfileForm(form) {
  const formData = new FormData(form);
  const customerForm = document.getElementById("customerForm");

  if (!formData.get("email") && customerForm) {
    formData.set("email", customerForm.querySelector("[name='email']").value);
  }

  if (!formData.get("fullname") && customerForm) {
    formData.set("fullname", customerForm.querySelector("[name='fullname']").value);
  }

  if (!formData.get("phone") && customerForm) {
    formData.set("phone", customerForm.querySelector("[name='phone']").value);
  }

  if (!formData.get("address") && customerForm) {
    formData.set("address", customerForm.querySelector("[name='address']").value);
  }

  fetch("/update-customer-profile", {
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

const customerForm = document.getElementById("customerForm");
const deliveryForm = document.getElementById("deliveryForm");
const passwordForm = document.getElementById("passwordForm");

customerForm?.addEventListener("submit", event => {
  event.preventDefault();
  submitProfileForm(customerForm);
});

deliveryForm?.addEventListener("submit", event => {
  event.preventDefault();
  submitProfileForm(deliveryForm);
});

passwordForm?.addEventListener("submit", event => {
  event.preventDefault();

  const formData = new FormData(passwordForm);

  if (formData.get("new_password") !== formData.get("confirm_password")) {
    alert("New password and confirm password do not match.");
    return;
  }

  fetch("/change-password", {
    method: "POST",
    body: formData
  })
    .then(response => response.json())
    .then(data => {
      alert(data.message);

      if (data.success) {
        passwordForm.reset();
      }
    });
});
