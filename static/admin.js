const announcementToggle = document.getElementById("announcementToggle");
const announcementPanel = document.getElementById("announcementPanel");
const closeAnnouncement = document.getElementById("closeAnnouncement");
const announcementMessages = document.querySelector(".announcement-messages");

announcementToggle?.addEventListener("click", () => {
  announcementPanel?.classList.remove("hidden");
  announcementMessages?.scrollTo(0, announcementMessages.scrollHeight);
});

closeAnnouncement?.addEventListener("click", () => {
  announcementPanel?.classList.add("hidden");
});

if (announcementMessages) {
  announcementMessages.scrollTop = announcementMessages.scrollHeight;
}
