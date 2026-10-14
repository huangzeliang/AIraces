/* 提醒中心（B2）页面脚本：全部已读二次确认、筛选自动提交、桌面通知开关（降级页面弹窗）、
   免打扰时段判断、页面弹窗提示。依赖：components/modal.js 提供的 window.NorthModal。 */

(function () {
  'use strict';

  var configNode = document.querySelector('#reminders-config');
  var CFG = { urls: {}, next: '', unread: 0, risk: 0, dnd_start: '', dnd_end: '' };
  if (configNode) {
    try { CFG = JSON.parse(configNode.textContent); } catch (error) { /* 保持默认值 */ }
  }
  var URLS = CFG.urls || {};
  var NEXT = CFG.next || '';
  var NOTIFY_KEY = 'northstar-desktop-notify';

  /* ---------------- 提示条 ---------------- */
  function ensureStack() {
    var stack = document.querySelector('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      document.body.appendChild(stack);
    }
    return stack;
  }

  function toast(message, isError) {
    var stack = ensureStack();
    var node = document.createElement('div');
    node.className = 'toast' + (isError ? ' is-error' : '');
    var text = document.createElement('span');
    text.textContent = message;
    node.appendChild(text);
    stack.appendChild(node);
    window.setTimeout(function () {
      node.classList.add('is-out');
      window.setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 420);
    }, 5600);
  }

  function submitHidden(action) {
    var form = document.createElement('form');
    form.method = 'post';
    form.action = action;
    form.style.display = 'none';
    var hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = 'next';
    hidden.value = NEXT;
    form.appendChild(hidden);
    document.body.appendChild(form);
    form.submit();
  }

  /* ---------------- 免打扰时段 ---------------- */
  function minutesOf(text) {
    var parts = String(text || '').split(':');
    if (parts.length < 2) return null;
    var hours = parseInt(parts[0], 10);
    var minutes = parseInt(parts[1], 10);
    if (isNaN(hours) || isNaN(minutes)) return null;
    return hours * 60 + minutes;
  }

  function inDnd() {
    var start = minutesOf(CFG.dnd_start);
    var end = minutesOf(CFG.dnd_end);
    if (start === null || end === null || start === end) return false;
    var now = new Date();
    var current = now.getHours() * 60 + now.getMinutes();
    if (start < end) return current >= start && current < end;
    return current >= start || current < end;  // 跨天时段
  }

  /* ---------------- 全部已读（二次确认） ---------------- */
  var readAll = document.querySelector('#read-all');
  if (readAll) {
    readAll.addEventListener('click', function () {
      if (!CFG.unread) {
        toast('当前没有未读提醒，无需批量操作。');
        return;
      }
      window.NorthModal.confirm({
        title: '全部标记已读',
        message: '确定把当前所有未读提醒标记为已读吗？',
        impact: '共 ' + CFG.unread + ' 条未读会被批量置为已读，顶栏角标随后清零；批量操作无法逐条撤销。',
        confirmText: '确认全部已读'
      }).then(function (ok) {
        if (ok) submitHidden(URLS.read_all || '');
      });
    });
  }

  /* ---------------- 筛选自动提交 ---------------- */
  var filterForm = document.querySelector('#reminder-filter');
  if (filterForm) {
    filterForm.querySelectorAll('input[type="checkbox"]').forEach(function (input) {
      input.addEventListener('change', function () { filterForm.submit(); });
    });
  }

  /* ---------------- 桌面通知开关（权限 → 降级页面弹窗） ---------------- */
  var notifySwitch = document.querySelector('#desktop-notify');
  var notifyLabel = document.querySelector('#notify-label');
  var notifySupported = typeof window.Notification === 'function';

  function syncNotifySwitch() {
    if (!notifySwitch) return;
    var enabled = false;
    try { enabled = window.localStorage.getItem(NOTIFY_KEY) === '1'; } catch (error) { enabled = false; }
    if (!notifySupported) {
      notifySwitch.checked = false;
      notifySwitch.disabled = true;
      if (notifyLabel) notifyLabel.textContent = '桌面通知（当前浏览器不支持）';
      return;
    }
    if (window.Notification.permission !== 'granted') {
      enabled = false;
      try { window.localStorage.setItem(NOTIFY_KEY, '0'); } catch (error) { /* 忽略 */ }
    }
    notifySwitch.checked = enabled;
    if (notifyLabel) notifyLabel.textContent = enabled ? '桌面通知已开启' : '桌面通知';
  }

  if (notifySwitch) {
    notifySwitch.addEventListener('change', function () {
      if (!notifySupported) return;
      if (!notifySwitch.checked) {
        try { window.localStorage.setItem(NOTIFY_KEY, '0'); } catch (error) { /* 忽略 */ }
        if (notifyLabel) notifyLabel.textContent = '桌面通知';
        toast('桌面通知已关闭，提醒仍会记录在页面列表。', false);
        return;
      }
      window.Notification.requestPermission().then(function (permission) {
        if (permission === 'granted') {
          try { window.localStorage.setItem(NOTIFY_KEY, '1'); } catch (error) { /* 忽略 */ }
          if (notifyLabel) notifyLabel.textContent = '桌面通知已开启';
          if (inDnd()) {
            toast('桌面通知已开启，但当前处于免打扰时段（' + CFG.dnd_start + '–' + CFG.dnd_end + '），提醒只记录不弹窗。', false);
          } else {
            toast('桌面通知已开启：新提醒会尝试在系统层面弹出。', false);
          }
        } else {
          notifySwitch.checked = false;
          try { window.localStorage.setItem(NOTIFY_KEY, '0'); } catch (error) { /* 忽略 */ }
          toast('浏览器已拒绝通知权限，已降级为页面弹窗提示。', true);
        }
      });
    });
    syncNotifySwitch();
  }

  /* ---------------- 页面弹窗（免打扰时段内只记录、不弹） ---------------- */
  window.setTimeout(function () {
    if (inDnd()) {
      toast('当前处于免打扰时段（' + CFG.dnd_start + '–' + CFG.dnd_end + '）：提醒照常记录，不会弹出打扰。', false);
      return;
    }
    if (CFG.risk > 0) {
      toast('页面弹窗：检测到 ' + CFG.risk + ' 条未读风险提醒（临期 / 逾期 / 依赖冲突），已置顶展示。', false);
    } else if (CFG.unread > 0) {
      toast('页面弹窗：有 ' + CFG.unread + ' 条未读提醒待处理。', false);
    }
  }, 500);

  /* ---------------- Flash 提示自动收起 ---------------- */
  document.querySelectorAll('.notice').forEach(function (node) {
    window.setTimeout(function () {
      node.classList.add('is-out');
      window.setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 450);
    }, 6000);
  });
})();
