// ================= MESSENGER =================

const messageToggle    = document.getElementById("messageToggle");
const messengerPanel   = document.getElementById("messengerPanel");
const collapseMessenger = document.getElementById("collapseMessenger");
const closeMessenger   = document.getElementById("closeMessenger");
const contacts         = document.querySelectorAll(".contact");
const chatTitle        = document.getElementById("chatTitle");

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


// ================= SUMMARY HELPER =================

function updateSummary(data) {
  const subtotalEl  = document.getElementById("summary-subtotal");
  const deliveryEl  = document.getElementById("summary-delivery");
  const totalEl     = document.getElementById("summary-total");
  const countEl     = document.getElementById("summary-count");

  if (subtotalEl && data.subtotal     !== undefined) subtotalEl.textContent = "₱" + data.subtotal.toFixed(2);
  if (deliveryEl && data.delivery_fee !== undefined) deliveryEl.textContent = "₱" + data.delivery_fee.toFixed(2);
  if (totalEl    && data.total        !== undefined) totalEl.textContent    = "₱" + data.total.toFixed(2);
  if (countEl)  countEl.textContent = document.querySelectorAll(".cart-card").length;
}

function moneyToNumber(text) {
  return parseFloat((text || "").replace(/[^\d.]/g, "")) || 0;
}

function updateSelectedSummary() {
  const selectedCards = Array.from(document.querySelectorAll(".cart-card"))
    .filter(card => card.querySelector(".select-item")?.checked);
  const subtotal = selectedCards.reduce((sum, card) => {
    const price = moneyToNumber(card.querySelector(".price")?.textContent);
    const quantity = parseInt(card.querySelector(".qty")?.textContent || "1", 10);
    return sum + price * quantity;
  }, 0);
  const deliveryFee = selectedCards.length ? 50 : 0;
  const itemCount = selectedCards.reduce((sum, card) => {
    return sum + parseInt(card.querySelector(".qty")?.textContent || "1", 10);
  }, 0);

  updateSummary({ subtotal, delivery_fee: deliveryFee, total: subtotal + deliveryFee });

  const countEl = document.getElementById("summary-count");
  const checkoutBtn = document.getElementById("checkoutSelected");
  const hint = document.getElementById("checkoutHint");

  if (countEl) countEl.textContent = itemCount;
  if (checkoutBtn) checkoutBtn.disabled = selectedCards.length === 0;
  if (hint) {
    hint.textContent = selectedCards.length
      ? `${selectedCards.length} selected plant${selectedCards.length === 1 ? "" : "s"} ready for checkout.`
      : "Select plants to checkout.";
  }
}


// ================= CART FUNCTIONALITY =================

document.querySelectorAll(".cart-card").forEach(card => {

  const itemId = parseInt(card.dataset.id); // parse as INT so backend receives a number

  // ---------- SIZE ----------
  const sizeBtns = card.querySelectorAll(".size-btn");

  sizeBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      sizeBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");

      fetch("/update_size", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          item_id: itemId,
          size:    btn.dataset.size          // uses data-size="Small/Medium/Large" on the button
        })
      });
    });
  });

  // ---------- QUANTITY ----------
  const minus   = card.querySelector(".minus");
  const plus    = card.querySelector(".plus");
  const qtySpan = card.querySelector(".qty");

  let qty = parseInt(qtySpan.textContent);

  plus.addEventListener("click", () => {
    qty++;
    updateQty();
  });

  minus.addEventListener("click", () => {
    if (qty > 1) {
      qty--;
      updateQty();
    }
  });

  function updateQty() {
    qtySpan.textContent = qty;

    fetch("/update_cart", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ item_id: itemId, quantity: qty })
    })
      .then(r => r.json())
      .then(() => updateSelectedSummary());   // refresh selected totals live
  }

  // ---------- REMOVE ----------
  const removeBtn = card.querySelector(".remove-btn");

  removeBtn.addEventListener("click", () => {
    fetch("/remove_item", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ item_id: itemId })
    })
      .then(r => r.json())
      .then(data => {
        card.remove();
        updateSelectedSummary();               // refresh totals after removal

        // show empty state if no cards left
        if (!document.querySelector(".cart-card")) {
          document.querySelector(".cart-items").innerHTML =
            '<div class="empty-cart"><p>Your cart is empty.</p>' +
            '<a href="/customer" class="btn-shop">Shop Now</a></div>';
        }
      });
  });

});


// ================= SELECT ALL =================

const selectAll = document.getElementById("selectAll");

if (selectAll) {
  const items = document.querySelectorAll(".select-item");
  selectAll.addEventListener("change", () => {
    items.forEach(item => item.checked = selectAll.checked);
    updateSelectedSummary();
  });
}

document.querySelectorAll(".select-item").forEach(item => {
  item.addEventListener("change", updateSelectedSummary);
});

const checkoutSelected = document.getElementById("checkoutSelected");
if (checkoutSelected) {
  checkoutSelected.addEventListener("click", () => {
    const selectedIds = Array.from(document.querySelectorAll(".cart-card"))
      .filter(card => card.querySelector(".select-item")?.checked)
      .map(card => card.dataset.id);

    if (!selectedIds.length) {
      updateSelectedSummary();
      return;
    }

    window.location.href = `/checkout?source=cart&items=${encodeURIComponent(selectedIds.join(","))}`;
  });
}

updateSelectedSummary();
