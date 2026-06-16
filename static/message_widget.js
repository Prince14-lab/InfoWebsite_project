(() => {
  const widget = document.querySelector(".message-widget[data-message-role]");
  if (!widget) return;

  const role = widget.dataset.messageRole;
  const isOwner = role === "owner";
  const isAdmin = role === "admin";
  const endpoints = isAdmin
    ? { load: "/admin/messages", send: "/admin/messages" }
    : isOwner
      ? { load: "/owner/messages", send: "/owner/messages" }
      : { load: "/customer/messages", send: "/customer/messages" };

  const toggle = document.getElementById("messageToggle");
  const panel = document.getElementById("messengerPanel");
  const collapse = document.getElementById("collapseMessenger");
  const close = document.getElementById("closeMessenger");
  const contacts = document.getElementById("messageContacts");
  const title = document.getElementById("chatTitle");
  const messagesBox = document.getElementById("chatMessages");
  const form = document.getElementById("messageForm");
  const input = document.getElementById("messageInput");
  const threadInput = document.getElementById("messageThreadId");
  const quickReplies = document.getElementById("quickReplies");
  let customerTarget = "owner";
  let ownerTarget = "customers";
  let adminTarget = "owner";
  let adminThreadId = "";
  const plantPalGreeting = "Hello! I'm PlantPal, your plant shopping assistant. How can I help you today? You can ask me about plants, cart, checkout, payment, delivery, or your purchases.";
  const ownerPalGreeting = "Hello! I'm OwnerPal, your nursery management assistant. I can help you review inventory, orders, sales, low-stock plants, customer concerns, and refund requests.";
  const adminPalGreeting = "Hello! I'm AdminPal, your system management assistant. I can help with customers, orders, reports, low stock alerts, and admin security.";
  const plantPalMessages = [{ body: plantPalGreeting, is_mine: false, attachments: [] }];
  const ownerPalMessages = [{ body: ownerPalGreeting, is_mine: false, attachments: [] }];
  const adminPalMessages = [{ body: adminPalGreeting, is_mine: false, attachments: [] }];

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value || "";
    return div.innerHTML;
  }

  function attachmentHtml(attachments) {
    if (!attachments || !attachments.length) return "";
    return `
      <div class="message-attachments">
        ${attachments.map(attachment => `
          <a href="${escapeHtml(attachment.file_url)}" target="_blank">
            <img src="${escapeHtml(attachment.file_url)}" alt="${escapeHtml(attachment.original_name || "Message photo")}">
          </a>
        `).join("")}
      </div>
    `;
  }

  function renderMessages(messages, emptyText) {
    if (!messagesBox) return;
    if (!messages || messages.length === 0) {
      messagesBox.innerHTML = `<div class="message received">${escapeHtml(emptyText)}</div>`;
      return;
    }
    messagesBox.innerHTML = messages.map(message => `
      <div class="message ${message.is_mine ? "sent" : "received"}">
        ${escapeHtml(message.body)}
        ${attachmentHtml(message.attachments)}
      </div>
    `).join("");
    messagesBox.scrollTop = messagesBox.scrollHeight;
  }

  function appendMessage(body, isMine) {
    if (!messagesBox) return;
    const message = document.createElement("div");
    message.className = `message ${isMine ? "sent" : "received"}`;
    message.innerHTML = escapeHtml(body);
    messagesBox.appendChild(message);
    messagesBox.scrollTop = messagesBox.scrollHeight;
  }

  function setupPhotoInput() {
    if (!form || document.getElementById("messagePhotos")) return;
    const photoInput = document.createElement("input");
    photoInput.type = "file";
    photoInput.id = "messagePhotos";
    photoInput.name = "photos";
    photoInput.accept = "image/*";
    photoInput.multiple = true;
    photoInput.hidden = true;

    const attachButton = document.createElement("button");
    attachButton.type = "button";
    attachButton.className = "attach-btn";
    attachButton.title = "Attach photos";
    attachButton.textContent = "Photo";

    const note = document.createElement("span");
    note.className = "attachment-note";
    note.id = "attachmentNote";

    attachButton.addEventListener("click", () => photoInput.click());
    photoInput.addEventListener("change", () => {
      const count = photoInput.files ? photoInput.files.length : 0;
      note.textContent = count ? `${count} photo${count > 1 ? "s" : ""}` : "";
    });

    form.insertBefore(photoInput, form.firstChild);
    form.insertBefore(attachButton, input?.nextSibling || form.firstChild);
    form.insertBefore(note, attachButton.nextSibling);
  }

  function clearPhotoInput() {
    const photoInput = document.getElementById("messagePhotos");
    const note = document.getElementById("attachmentNote");
    if (photoInput) photoInput.value = "";
    if (note) note.textContent = "";
  }

  function setPhotoToolsVisible(isVisible) {
    const photoButton = form?.querySelector(".attach-btn");
    const note = document.getElementById("attachmentNote");
    if (photoButton) photoButton.style.display = isVisible ? "" : "none";
    if (note) note.style.display = isVisible ? "" : "none";
  }

  function renderOwnerThreads(threads, selectedThreadId) {
    if (!contacts) return;
    const threadItems = (threads || []).map(thread => `
      <div class="contact ${ownerTarget === "customers" && thread.thread_id === selectedThreadId ? "active" : ""}"
           data-thread-id="${thread.thread_id}"
           data-chat-target="customers">
        <img src="${escapeHtml(thread.photo)}" alt="${escapeHtml(thread.customer_name)}">
        <span>${escapeHtml(thread.customer_name)}</span>
      </div>
    `).join("");
    const emptyCustomers = threads && threads.length ? "" : `
      <div class="contact ${ownerTarget === "customers" ? "active" : ""}" data-chat-target="customers">
        <img src="/static/default-profile.jpg" alt="Customers">
        <span>No inquiries yet</span>
      </div>
    `;
    contacts.innerHTML = `
      <div class="contact ${ownerTarget === "announcements" ? "active" : ""}" data-chat-target="announcements">
        <img src="/static/announcement-profile.svg" alt="Announcements">
        <span>Announcements</span>
      </div>
      ${threadItems || emptyCustomers}
      <div class="contact ${ownerTarget === "ownerpal" ? "active" : ""}" data-chat-target="ownerpal">
        <img src="/static/ownerpal-profile.svg" alt="OwnerPal">
        <span>OwnerPal</span>
      </div>
    `;
    contacts.querySelectorAll(".contact[data-thread-id]").forEach(contact => {
      contact.addEventListener("click", () => {
        ownerTarget = "customers";
        loadMessages(contact.dataset.threadId);
      });
    });
    contacts.querySelectorAll(".contact[data-chat-target='announcements']").forEach(contact => {
      contact.addEventListener("click", () => selectOwnerTarget("announcements"));
    });
    contacts.querySelectorAll(".contact[data-chat-target='ownerpal']").forEach(contact => {
      contact.addEventListener("click", () => selectOwnerTarget("ownerpal"));
    });
  }

  function renderAdminThreads(threads, selectedThreadId) {
    if (!contacts) return;
    const ownerThread = threads && threads.length ? threads[0] : null;
    contacts.innerHTML = `
      <div class="contact ${adminTarget === "owner" ? "active" : ""}"
           data-chat-target="owner"
           ${ownerThread ? `data-thread-id="${ownerThread.thread_id}"` : ""}>
        <img src="${escapeHtml(ownerThread?.photo || "/static/default-profile.jpg")}" alt="${escapeHtml(ownerThread?.reporter_name || "Owner")}">
        <span>${escapeHtml(ownerThread?.reporter_name || "Owner")}</span>
      </div>
      <div class="contact ${adminTarget === "adminpal" ? "active" : ""}" data-chat-target="adminpal">
        <div class="adminpal-contact-avatar">AP</div>
        <span>AdminPal</span>
      </div>
    `;
    contacts.querySelectorAll(".contact[data-chat-target='owner']").forEach(contact => {
      contact.addEventListener("click", () => {
        adminTarget = "owner";
        loadMessages(contact.dataset.threadId || "");
      });
    });
    contacts.querySelectorAll(".contact[data-chat-target='adminpal']").forEach(contact => {
      contact.addEventListener("click", () => selectAdminTarget("adminpal"));
    });
  }

  function ensureAdminPalTools() {
    let replies = document.getElementById("adminQuickReplies");
    if (!replies) {
      replies = document.createElement("div");
      replies.id = "adminQuickReplies";
      replies.className = "admin-quick-replies";
      replies.innerHTML = [
        "System summary",
        "Show pending reports",
        "Check active orders",
        "Blocked customers",
        "Low stock alerts",
        "What does admin do?"
      ].map(reply => `<button class="admin-quick-reply-btn" type="button">${reply}</button>`).join("");
      form?.insertAdjacentElement("beforebegin", replies);
      replies.querySelectorAll(".admin-quick-reply-btn").forEach(button => {
        button.addEventListener("click", () => {
          if (input) input.value = button.textContent || "";
          form?.requestSubmit();
        });
      });
    }
    return { replies };
  }

  function setAdminPalVisible(isVisible) {
    if (!isAdmin) return;
    const { replies } = ensureAdminPalTools();
    if (replies) replies.style.display = isVisible ? "flex" : "none";
    setPhotoToolsVisible(!isVisible);
  }

  function renderAdminPalGreeting() {
    renderMessages(adminPalMessages, "");
  }

  function selectAdminTarget(target) {
    adminTarget = target || "owner";
    contacts?.querySelectorAll(".contact[data-chat-target]").forEach(contactItem => {
      contactItem.classList.toggle("active", contactItem.dataset.chatTarget === adminTarget);
    });

    if (adminTarget === "adminpal") {
      if (title) title.textContent = "AdminPal";
      if (threadInput) threadInput.value = "";
      setAdminPalVisible(true);
      renderAdminPalGreeting();
      return;
    }

    setAdminPalVisible(false);
    loadMessages();
  }

  function renderCustomerContact(data) {
    if (!contacts) return;
    const ownerContact = data.owner_contact || data.contact;
    contacts.innerHTML = `
      <div class="contact ${customerTarget === "announcements" ? "active" : ""}" data-chat-target="announcements">
        <img src="/static/announcement-profile.svg" alt="Announcements">
        <span>Announcements</span>
      </div>
      <div class="contact ${customerTarget === "owner" ? "active" : ""}" data-chat-target="owner">
        <img src="${escapeHtml(ownerContact?.photo || "/static/default-profile.jpg")}" alt="${escapeHtml(ownerContact?.name || "Green Owner")}">
        <span>${escapeHtml(ownerContact?.name || "Green Owner")}</span>
      </div>
      <div class="contact ${customerTarget === "plantpal" ? "active" : ""}" data-chat-target="plantpal">
        <img src="/static/plantpal-profile.svg" alt="PlantPal">
        <span>PlantPal</span>
      </div>
    `;
    contacts.querySelectorAll(".contact[data-chat-target]").forEach(contactItem => {
      contactItem.addEventListener("click", () => selectCustomerTarget(contactItem.dataset.chatTarget));
    });
  }

  function setQuickRepliesVisible(isVisible) {
    if (quickReplies) quickReplies.style.display = isVisible ? "flex" : "none";
  }

  function ensureOwnerPalTools() {
    let replies = document.getElementById("ownerQuickReplies");
    if (!replies) {
      replies = document.createElement("div");
      replies.id = "ownerQuickReplies";
      replies.className = "owner-quick-replies";
      replies.innerHTML = [
        "Business summary",
        "Check low stock plants",
        "Show pending orders",
        "Show best sellers",
        "Show total sales",
        "Inventory advice",
        "Pricing advice",
        "Customer support tips",
        "How do I update order status?"
      ].map(reply => `<button class="owner-quick-reply-btn" type="button">${reply}</button>`).join("");
      form?.insertAdjacentElement("beforebegin", replies);
      replies.querySelectorAll(".owner-quick-reply-btn").forEach(button => {
        button.addEventListener("click", () => {
          if (input) input.value = button.textContent || "";
          form?.requestSubmit();
        });
      });
    }
    return { replies };
  }

  function setOwnerPalVisible(isVisible) {
    if (!isOwner) return;
    const { replies } = ensureOwnerPalTools();
    if (replies) replies.style.display = isVisible ? "flex" : "none";
    setPhotoToolsVisible(!isVisible);
  }

  function renderOwnerPalGreeting() {
    renderMessages(ownerPalMessages, "");
  }

  async function loadAnnouncements() {
    if (title) title.textContent = "Announcements";
    setQuickRepliesVisible(false);
    setOwnerPalVisible(false);
    setPhotoToolsVisible(false);
    try {
      const response = await fetch("/announcements");
      const data = await response.json();
      if (!data.success) {
        renderMessages([], data.message || "Unable to load announcements.");
        return;
      }
      renderMessages(data.announcements, "No announcements yet.");
    } catch (error) {
      renderMessages([], "Unable to load announcements right now.");
    }
  }

  async function selectOwnerTarget(target) {
    ownerTarget = target || "customers";
    contacts?.querySelectorAll(".contact[data-chat-target]").forEach(contactItem => {
      contactItem.classList.toggle("active", contactItem.dataset.chatTarget === ownerTarget);
    });

    if (ownerTarget === "ownerpal") {
      if (title) title.textContent = "OwnerPal";
      if (threadInput) threadInput.value = "";
      setOwnerPalVisible(true);
      renderOwnerPalGreeting();
      return;
    }

    if (ownerTarget === "announcements") {
      if (threadInput) threadInput.value = "";
      await loadAnnouncements();
      return;
    }

    setOwnerPalVisible(false);
    await loadMessages("", ownerTarget);
  }

  async function selectCustomerTarget(target) {
    customerTarget = target || "owner";
    contacts?.querySelectorAll(".contact[data-chat-target]").forEach(contactItem => {
      contactItem.classList.toggle("active", contactItem.dataset.chatTarget === customerTarget);
    });

    if (customerTarget === "plantpal") {
      if (title) title.textContent = "PlantPal";
      setQuickRepliesVisible(true);
      setPhotoToolsVisible(false);
      renderMessages(plantPalMessages, "");
      return;
    }

    if (customerTarget === "announcements") {
      await loadAnnouncements();
      return;
    }

    setQuickRepliesVisible(false);
    setPhotoToolsVisible(true);
    await loadMessages("", customerTarget);
  }

  async function loadMessages(threadId = "", targetOverride = "") {
    if (!messagesBox) return;
    const params = new URLSearchParams();
    if (threadId) params.set("thread_id", threadId);
    if (isOwner && targetOverride) params.set("target", targetOverride);
    const url = params.toString() ? `${endpoints.load}?${params.toString()}` : endpoints.load;

    try {
      const response = await fetch(url);
      const data = await response.json();
      if (!data.success) {
        renderMessages([], data.message || "Unable to load messages.");
        return;
      }

      if (isAdmin) {
        if (adminTarget === "adminpal") return;
        adminThreadId = data.selected_thread_id || "";
        if (threadInput) threadInput.value = adminThreadId;
        renderAdminThreads(data.threads, data.selected_thread_id);
        setAdminPalVisible(false);
        if (title) title.textContent = data.selected_reporter?.name || "Reports";
        renderMessages(data.messages, "Your conversation with the owner will appear here.");
        return;
      }

      if (isOwner) {
        if (ownerTarget === "ownerpal") return;
        if (ownerTarget === "announcements") return;
        renderOwnerThreads(data.threads, data.selected_thread_id);
        setOwnerPalVisible(false);
        if (threadInput) threadInput.value = data.selected_thread_id || "";
        if (title) title.textContent = data.selected_customer?.name || "Customers";
        renderMessages(data.messages, "Select a customer inquiry or wait for new messages.");
      } else {
        renderCustomerContact(data);
        if (title) title.textContent = data.contact?.name || "Green Owner";
        renderMessages(
          data.messages,
          "Send a message to start a conversation with the owner."
        );
      }
    } catch (error) {
      renderMessages([], "Unable to load messages right now.");
    }
  }

  if (toggle && panel) {
    toggle.addEventListener("click", event => {
      event.stopImmediatePropagation();
      panel.classList.remove("hidden");
      panel.classList.add("expanded");
      panel.classList.remove("collapsed");
      toggle.style.display = "none";
      loadMessages();
    }, true);
  }

  if (collapse && panel) {
    collapse.addEventListener("click", event => {
      event.stopImmediatePropagation();
      const isExpanded = panel.classList.contains("expanded");
      panel.classList.toggle("expanded", !isExpanded);
      panel.classList.toggle("collapsed", isExpanded);
      panel.classList.remove("hidden");
    }, true);
  }

  if (close && panel && toggle) {
    close.addEventListener("click", event => {
      event.stopImmediatePropagation();
      panel.classList.add("hidden");
      panel.classList.remove("collapsed", "expanded");
      toggle.style.display = "inline-block";
    }, true);
  }

  if (form) {
    setupPhotoInput();
    form.addEventListener("submit", async event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const body = (input?.value || "").trim();
      const photoInput = document.getElementById("messagePhotos");
      const hasPhotos = photoInput?.files && photoInput.files.length > 0;
      if (!body && !hasPhotos) return;

      if (isOwner && ownerTarget === "ownerpal") {
        input.value = "";
        clearPhotoInput();
        ownerPalMessages.push({ body, is_mine: true, attachments: [] });
        appendMessage(body, true);
        try {
          const response = await fetch("/owner-chatbot", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: body }),
          });
          const data = await response.json();
          const reply = data.reply || "OwnerPal is here to help, but I could not understand that yet.";
          ownerPalMessages.push({ body: reply, is_mine: false, attachments: [] });
          appendMessage(reply, false);
        } catch (error) {
          const reply = "Sorry, OwnerPal is having trouble replying right now. Please try again in a moment.";
          ownerPalMessages.push({ body: reply, is_mine: false, attachments: [] });
          appendMessage(reply, false);
        }
        return;
      }

      if (isAdmin && adminTarget === "adminpal") {
        input.value = "";
        clearPhotoInput();
        adminPalMessages.push({ body, is_mine: true, attachments: [] });
        appendMessage(body, true);
        try {
          const response = await fetch("/admin-chatbot", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: body }),
          });
          const data = await response.json();
          const reply = data.reply || "AdminPal is here to help, but I could not understand that yet.";
          adminPalMessages.push({ body: reply, is_mine: false, attachments: [] });
          appendMessage(reply, false);
        } catch (error) {
          const reply = "Sorry, AdminPal is having trouble replying right now. Please try again in a moment.";
          adminPalMessages.push({ body: reply, is_mine: false, attachments: [] });
          appendMessage(reply, false);
        }
        return;
      }

      if (!isOwner && !isAdmin && customerTarget === "plantpal") {
        input.value = "";
        clearPhotoInput();
        plantPalMessages.push({ body, is_mine: true, attachments: [] });
        appendMessage(body, true);
        try {
          const response = await fetch("/customer-chatbot", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: body }),
          });
          const data = await response.json();
          const reply = data.reply || "PlantPal is here to help, but I could not understand that yet.";
          plantPalMessages.push({ body: reply, is_mine: false, attachments: [] });
          appendMessage(reply, false);
        } catch (error) {
          const reply = "Sorry, PlantPal is having trouble replying right now. Please try again in a moment.";
          plantPalMessages.push({ body: reply, is_mine: false, attachments: [] });
          appendMessage(reply, false);
        }
        return;
      }

      const formData = new FormData();
      formData.append("message", body);
      if (isAdmin && threadInput?.value) formData.append("thread_id", threadInput.value);
      if (isOwner) {
        formData.append("target", ownerTarget);
        if (threadInput?.value) formData.append("thread_id", threadInput.value);
      }
      if (isOwner && ownerTarget === "announcements") return;
      if (!isOwner && !isAdmin && customerTarget === "announcements") return;
      if (!isOwner && !isAdmin) formData.append("target", "owner");
      Array.from(photoInput?.files || []).forEach(file => formData.append("photos", file));
      input.value = "";
      clearPhotoInput();

      const response = await fetch(endpoints.send, { method: "POST", body: formData });
      const data = await response.json();
      if (data.success) {
        renderMessages(data.messages, "");
        if (isAdmin && threadInput?.value) loadMessages(threadInput.value);
        if (isOwner && ownerTarget === "customers" && threadInput?.value) loadMessages(threadInput.value);
      } else {
        appendMessage(data.message || "Unable to send message.", false);
      }
    }, true);
  }

  if (quickReplies) {
    quickReplies.querySelectorAll(".quick-reply-btn").forEach(button => {
      button.addEventListener("click", () => {
        if (input) input.value = button.textContent || "";
        form?.requestSubmit();
      });
    });
  }

  loadMessages();
})();
