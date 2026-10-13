/* 系统设置（C3）页面脚本：主题（复用公共 theme.js）、视图角色、渠道占位、吉祥物与快捷键。
   零依赖实现；主题切换不重复实现逻辑，统一触发顶栏的主题按钮。 */

(function () {
  'use strict';

  var hintNode = document.querySelector('#save-hint');

  function flashHint(message, isError) {
    if (!hintNode) return;
    hintNode.textContent = message;
    hintNode.className = 'action-hint' + (isError ? ' is-error' : '');
    window.setTimeout(function () {
      hintNode.textContent = '';
    }, 3600);
  }

  /* ---------------- 主题：复用顶栏按钮的逻辑 ---------------- */
  var themeSwitch = document.querySelector('#settings-theme');
  var themeText = document.querySelector('#theme-text');
  var themeValue = document.querySelector('#theme-value');
  var themeToggle = document.querySelector('#theme-toggle');

  function syncThemeControls() {
    var isNight = document.documentElement.dataset.theme === 'night';
    if (themeSwitch) themeSwitch.checked = isNight;
    if (themeText) themeText.textContent = isNight ? '夜晚' : '白天';
    if (themeValue) themeValue.value = isNight ? 'night' : 'day';
  }

  if (themeSwitch) {
    themeSwitch.addEventListener('change', function () {
      // 交给公共 theme.js 完成切换，再按实际主题回写本页控件状态
      if (themeToggle) themeToggle.click();
      syncThemeControls();
    });
    syncThemeControls();
  }

  /* ---------------- 视图角色：普通用户隐藏管理操作 ---------------- */
  var roleSelect = document.querySelector('#role-select');

  function syncRoleView() {
    var isAdmin = roleSelect && roleSelect.value === '管理员';
    document.querySelectorAll('.admin-only').forEach(function (node) {
      node.classList.toggle('is-hidden', !isAdmin);
    });
  }

  if (roleSelect) {
    roleSelect.addEventListener('change', function () {
      syncRoleView();
      flashHint('已切换到「' + roleSelect.value + '」视图，保存后写入配置。', false);
    });
    syncRoleView();
  }

  /* ---------------- 渠道：测试发送为占位 ---------------- */
  document.querySelectorAll('.test-channel').forEach(function (button) {
    button.addEventListener('click', function () {
      flashHint('「' + button.dataset.name + '」为演示占位，环境不真实投递，保存后仅记录配置。', false);
    });
  });

  /* ---------------- AI 吉祥物（演示级） ---------------- */
  var mascot = document.querySelector('#mascot');
  var mascotCaption = document.querySelector('#mascot-caption');
  var mascotText = {
    idle: '空闲 · 一切正常',
    busy: '繁忙 · 正在处理任务',
    warn: '警告 · 有任务需要关注'
  };

  document.querySelectorAll('[data-mascot]').forEach(function (button) {
    button.addEventListener('click', function () {
      var state = button.dataset.mascot;
      if (mascot) mascot.setAttribute('data-state', state);
      if (mascotCaption) mascotCaption.textContent = mascotText[state] || '';
    });
  });

  /* ---------------- 快捷键（G 系列） ---------------- */
  var pendingLeader = false;
  var leaderTimer = null;
  var shortcuts = { d: '/dashboard', m: '/data', s: '/settings' };

  document.addEventListener('keydown', function (event) {
    var tag = (event.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return;

    if (event.key === 'g' || event.key === 'G') {
      pendingLeader = true;
      window.clearTimeout(leaderTimer);
      leaderTimer = window.setTimeout(function () { pendingLeader = false; }, 1200);
      return;
    }
    if (pendingLeader) {
      var target = shortcuts[(event.key || '').toLowerCase()];
      pendingLeader = false;
      window.clearTimeout(leaderTimer);
      if (target) window.location.href = target;
    }
  });
})();
