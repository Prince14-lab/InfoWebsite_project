// QUANTITY
let quantity = 1;
const quantityDisplay = document.getElementById("quantityValue");
const quantityInputs = document.querySelectorAll(".selected-quantity");
const sizeInputs = document.querySelectorAll(".selected-size");

function syncQuantityInputs() {
  quantityInputs.forEach(input => {
    input.value = quantity;
  });
}

function syncSizeInputs(size) {
  sizeInputs.forEach(input => {
    input.value = size;
  });
}

document.getElementById("increaseQty").onclick = () => {
  quantity++;
  quantityDisplay.textContent = quantity;
  syncQuantityInputs();
};

document.getElementById("decreaseQty").onclick = () => {
  if (quantity > 1) {
    quantity--;
    quantityDisplay.textContent = quantity;
    syncQuantityInputs();
  }
};

// SIZE SELECT
const sizeButtons = document.querySelectorAll(".size-btn");

sizeButtons.forEach(btn => {
  btn.addEventListener("click", () => {
    sizeButtons.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    syncSizeInputs(btn.dataset.size);
  });
});

// SAMPLE IMAGE SELECT
const mainPlantImage = document.getElementById("mainPlantImage");
const sampleButtons = document.querySelectorAll(".sample-image");

sampleButtons.forEach(button => {
  button.addEventListener("click", () => {
    sampleButtons.forEach(sample => sample.classList.remove("active"));
    button.classList.add("active");

    if (mainPlantImage) {
      mainPlantImage.src = button.dataset.image;
    }
  });
});
