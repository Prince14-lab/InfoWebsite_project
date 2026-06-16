const messageToggle = document.getElementById("messageToggle");
const messengerPanel = document.getElementById("messengerPanel");
const collapseMessenger = document.getElementById("collapseMessenger");
const closeMessenger = document.getElementById("closeMessenger");
const chatTitle = document.getElementById("chatTitle");
const chatMessages = document.getElementById("chatMessages");
const messageContacts = document.getElementById("messageContacts");
const messageForm = document.getElementById("messageForm");
const messageInput = document.getElementById("messageInput");
const messageThreadId = document.getElementById("messageThreadId");

// ── MESSENGER ─────────────────────────────────────────────────────────────────

messageToggle.addEventListener("click", () => {
  messengerPanel.classList.remove("hidden");
  messengerPanel.classList.add("expanded");
  messengerPanel.classList.remove("collapsed");
  messageToggle.style.display = "none";
  loadOwnerMessages();
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

function renderOwnerMessages(messages) {
  if (!chatMessages) return;

  if (!messages || messages.length === 0) {
    chatMessages.innerHTML = '<div class="message received">Select a customer inquiry or wait for new messages.</div>';
    return;
  }

  chatMessages.innerHTML = messages.map(message => `
    <div class="message ${message.is_mine ? "sent" : "received"}">
      ${escapeHtml(message.body)}
    </div>
  `).join("");
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function renderOwnerThreads(threads, selectedThreadId) {
  if (!messageContacts) return;

  if (!threads || threads.length === 0) {
    messageContacts.innerHTML = `
      <div class="contact active" data-name="Customers">
        <img src="/static/default-profile.jpg" alt="Customers">
        <span>No inquiries yet</span>
      </div>
    `;
    return;
  }

  messageContacts.innerHTML = threads.map(thread => `
    <div class="contact ${thread.thread_id === selectedThreadId ? "active" : ""}"
         data-thread-id="${thread.thread_id}"
         data-name="${escapeHtml(thread.customer_name)}">
      <img src="${escapeHtml(thread.photo)}" alt="${escapeHtml(thread.customer_name)}">
      <span>${escapeHtml(thread.customer_name)}</span>
    </div>
  `).join("");

  messageContacts.querySelectorAll(".contact[data-thread-id]").forEach(contact => {
    contact.addEventListener("click", () => {
      loadOwnerMessages(contact.dataset.threadId);
    });
  });
}

async function loadOwnerMessages(threadId = "") {
  if (!chatMessages) return;

  const url = threadId ? `/owner/messages?thread_id=${encodeURIComponent(threadId)}` : "/owner/messages";
  try {
    const response = await fetch(url);
    const data = await response.json();
    if (!data.success) {
      chatMessages.innerHTML = `<div class="message received">${escapeHtml(data.message || "Unable to load messages.")}</div>`;
      return;
    }

    renderOwnerThreads(data.threads, data.selected_thread_id);
    if (messageThreadId) messageThreadId.value = data.selected_thread_id || "";
    if (chatTitle) chatTitle.textContent = data.selected_customer?.name || "Customers";
    renderOwnerMessages(data.messages);
  } catch (error) {
    chatMessages.innerHTML = '<div class="message received">Unable to load customer inquiries right now.</div>';
  }
}

if (messageForm) {
  messageForm.addEventListener("submit", async event => {
    event.preventDefault();
    const body = (messageInput?.value || "").trim();
    const threadId = messageThreadId?.value;
    if (!body || !threadId) return;

    const formData = new FormData();
    formData.append("thread_id", threadId);
    formData.append("message", body);
    messageInput.value = "";

    const response = await fetch("/owner/messages", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (data.success) {
      renderOwnerMessages(data.messages);
      loadOwnerMessages(threadId);
    }
  });
}

loadOwnerMessages();


// ── SALES OVERVIEW CHART ──────────────────────────────────────────────────────

(function initSalesChart() {
  const canvas = document.getElementById("salesChart");
  const periodSelect = document.getElementById("salesPeriodSelect");
  const selectedLabel = document.getElementById("salesSelectedLabel");
  const selectedTotal = document.getElementById("salesSelectedTotal");
  const dataScript = document.getElementById("salesDataJson");

  if (!canvas || !periodSelect || !dataScript) return;

  const salesData = JSON.parse(dataScript.textContent);

  const labelMap = {
    day: "Today",
    week: "This Week",
    month: "This Month",
    year: "This Year"
  };

  function formatPeso(value) {
    return "₱" + Number(value || 0).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  const chart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: [],
      datasets: [{
        label: "Sales",
        data: [],
        backgroundColor: "#4CAF50",
        borderColor: "#2e7d32",
        borderWidth: 1,
        borderRadius: 10,
        barThickness: 38,
        maxBarThickness: 52
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => formatPeso(ctx.parsed.y)
          }
        }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            color: "#4b604d",
            font: { size: 12, weight: "bold" }
          }
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(46, 125, 50, 0.10)" },
          ticks: {
            color: "#6d7f70",
            callback: value => formatPeso(value)
          }
        }
      }
    }
  });

  function updateChart(period) {
    const selectedData = salesData[period] || { labels: [], values: [] };
    const labels = selectedData.labels || [];
    const values = selectedData.values || [];

    chart.data.labels = labels.length ? labels : ["No sales yet"];
    chart.data.datasets[0].data = values.length ? values : [0];
    chart.update();

    const total = values.reduce((sum, value) => sum + Number(value || 0), 0);

    if (selectedLabel) selectedLabel.textContent = labelMap[period];
    if (selectedTotal) selectedTotal.textContent = formatPeso(total);
  }

  updateChart(periodSelect.value || "month");

  periodSelect.addEventListener("change", () => {
    updateChart(periodSelect.value);
  });
})();
