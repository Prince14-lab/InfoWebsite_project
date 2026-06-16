const showAdminInfo = document.getElementById("showAdminInfo");
const showUsernameSection = document.getElementById("showUsernameSection");
const showPasswordSection = document.getElementById("showPasswordSection");

const adminInfoSection = document.getElementById("adminInfoSection");
const usernameSection = document.getElementById("usernameSection");
const passwordSection = document.getElementById("passwordSection");

const buttons = [showAdminInfo, showUsernameSection, showPasswordSection].filter(Boolean);
const sections = [adminInfoSection, usernameSection, passwordSection].filter(Boolean);

function showSection(sectionToShow, buttonToActivate) {
  if (!sectionToShow || !buttonToActivate) {
    return;
  }

  sections.forEach((section) => {
    section.classList.remove("active-section");
    section.classList.add("hidden-section");
  });

  buttons.forEach((button) => {
    button.classList.remove("active-tab");
  });

  sectionToShow.classList.remove("hidden-section");
  sectionToShow.classList.add("active-section");
  buttonToActivate.classList.add("active-tab");
}

showAdminInfo?.addEventListener("click", () => {
  showSection(adminInfoSection, showAdminInfo);
});

showUsernameSection?.addEventListener("click", () => {
  showSection(usernameSection, showUsernameSection);
});

showPasswordSection?.addEventListener("click", () => {
  showSection(passwordSection, showPasswordSection);
});

showSection(adminInfoSection, showAdminInfo);
