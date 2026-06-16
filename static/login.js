const loginBtn = document.getElementById("loginBtn");
const signupBtn = document.getElementById("signupBtn");

const loginForm = document.getElementById("loginForm");
const signupForm = document.getElementById("signupForm");

const showSignup = document.getElementById("showSignup");
const showLogin = document.getElementById("showLogin");

function showLoginForm() {
  loginForm.classList.remove("hidden");
  signupForm.classList.add("hidden");
  loginBtn.classList.add("active");
  signupBtn.classList.remove("active");
}

function showSignupForm() {
  signupForm.classList.remove("hidden");
  loginForm.classList.add("hidden");
  signupBtn.classList.add("active");
  loginBtn.classList.remove("active");
}

loginBtn.addEventListener("click", showLoginForm);
signupBtn.addEventListener("click", showSignupForm);
showSignup.addEventListener("click", showSignupForm);
showLogin.addEventListener("click", showLoginForm);