/* 登录页交互：密码显隐、演示账号一键填入、提交防重复。 */
(function () {
  'use strict';

  var form = document.querySelector('#login-form');
  var emailInput = form && form.querySelector('input[name="email"]');
  var passwordInput = document.querySelector('#login-password');
  var toggle = document.querySelector('#password-toggle');
  var submit = document.querySelector('#login-submit');

  if (toggle && passwordInput) {
    toggle.addEventListener('click', function () {
      var show = passwordInput.type === 'password';
      passwordInput.type = show ? 'text' : 'password';
      toggle.textContent = show ? '隐藏' : '显示';
      toggle.setAttribute('aria-pressed', String(show));
      toggle.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
      passwordInput.focus();
    });
  }

  document.querySelectorAll('.demo-fill').forEach(function (button) {
    button.addEventListener('click', function () {
      if (emailInput) { emailInput.value = button.dataset.email || ''; }
      if (passwordInput) { passwordInput.value = button.dataset.password || ''; passwordInput.type = 'password'; }
      if (toggle) { toggle.textContent = '显示'; toggle.setAttribute('aria-pressed', 'false'); }
      if (submit) { submit.focus(); }
    });
  });

  if (form && submit) {
    form.addEventListener('submit', function () {
      submit.disabled = true;
      submit.textContent = '登录中…';
    });
    window.addEventListener('pageshow', function () {
      submit.disabled = false;
      submit.textContent = '登 录';
    });
  }
})();
