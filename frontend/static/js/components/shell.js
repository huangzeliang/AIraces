/* 工作台壳层交互：移动端抽屉导航 + 会话过期自动回到登录页。 */
(function () {
  'use strict';

  var button = document.querySelector('#mobile-menu-button');
  var nav = document.querySelector('#mobile-nav');
  var panel = document.querySelector('#mobile-nav-panel');
  var closeButton = document.querySelector('#mobile-nav-close');

  function isOpen() {
    return Boolean(nav) && !nav.hidden;
  }

  function openNav() {
    if (!nav || isOpen()) { return; }
    nav.hidden = false;
    document.body.classList.add('nav-open');
    if (button) { button.setAttribute('aria-expanded', 'true'); }
    var target = closeButton || panel.querySelector('a, button');
    if (target) { target.focus(); }
  }

  function closeNav() {
    if (!nav || !isOpen()) { return; }
    nav.hidden = true;
    document.body.classList.remove('nav-open');
    if (button) {
      button.setAttribute('aria-expanded', 'false');
      button.focus();
    }
  }

  if (button && nav && panel) {
    button.addEventListener('click', openNav);
    if (closeButton) { closeButton.addEventListener('click', closeNav); }
    var mask = nav.querySelector('[data-nav-close]');
    if (mask) { mask.addEventListener('click', closeNav); }
    panel.querySelectorAll('a.nav-item').forEach(function (link) {
      link.addEventListener('click', function () { closeNav(); });
    });

    document.addEventListener('keydown', function (event) {
      if (!isOpen()) { return; }
      if (event.key === 'Escape') {
        event.preventDefault();
        closeNav();
        return;
      }
      if (event.key !== 'Tab') { return; }
      var focusables = panel.querySelectorAll('a[href], button:not([disabled])');
      if (!focusables.length) { return; }
      var first = focusables[0];
      var last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    var wide = window.matchMedia('(min-width: 701px)');
    var onWide = function (event) { if (event.matches) { closeNav(); } };
    if (wide.addEventListener) { wide.addEventListener('change', onWide); } else { wide.addListener(onWide); }
  }

  // 会话过期：接口返回 401 时回到登录页（登录页自身除外）。
  // NS 线页面在 <html data-login-url="/ns/login"> 上声明自己的登录页，默认仍为旧系统 /login。
  var loginUrl = document.documentElement.getAttribute('data-login-url') || '/login';
  if (window.location.pathname !== loginUrl && typeof window.fetch === 'function') {
    var originalFetch = window.fetch.bind(window);
    window.fetch = function () {
      return originalFetch.apply(null, arguments).then(function (response) {
        if (response.status === 401) {
          window.location.href = loginUrl;
        }
        return response;
      });
    };
  }
})();
