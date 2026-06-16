const navbar = document.querySelector('.navbar');
const plantsSection = document.querySelector('#plants');

function handleNavbarColor() {
  const sectionTop = plantsSection.offsetTop - navbar.offsetHeight;

  if (window.scrollY >= sectionTop) {
    navbar.classList.add('green');
  } else {
    navbar.classList.remove('green');
  }
}

window.addEventListener('scroll', handleNavbarColor);
window.addEventListener('load', handleNavbarColor);

/* FIXED NAVIGATION SCROLL */
document.querySelectorAll('.navbar a[href^="#"]').forEach(link => {
  link.addEventListener('click', function(e) {
    e.preventDefault();

    const targetId = this.getAttribute('href');
    const targetSection = document.querySelector(targetId);

    if (!targetSection) return;

    const navbarHeight = document.querySelector('.navbar').offsetHeight;

    const targetPosition =
      targetSection.getBoundingClientRect().top +
      window.pageYOffset -
      navbarHeight + 80; // Additional offset for spacing

    window.scrollTo({
      top: targetPosition,
      behavior: "smooth"
    });
  });
});