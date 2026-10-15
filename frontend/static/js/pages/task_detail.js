/* 单任务详情（F-116 / F-117）页面脚本：迁出二次确认、拆解表单辅助与工时余额提示。
   依赖：components/modal.js 提供的 window.NorthModal（B4 公共组件）。零依赖其余实现。 */

(function () {
  'use strict';

  var CFG = { detach: '', remaining: null };
  var configNode = document.querySelector('#task-detail-config');
  if (configNode) {
    try { CFG = JSON.parse(configNode.textContent); } catch (error) { /* 保持默认值 */ }
  }

  /* ---------------- 迁出：二次确认后提交 ---------------- */
  document.addEventListener('click', function (event) {
    var button = event.target.closest('button[data-action="detach"]');
    if (!button) return;
    event.preventDefault();
    var form = button.closest('form');
    var title = button.dataset.title || '该子任务';
    window.NorthModal.confirm({
      title: '迁出子任务',
      message: '确定把「' + title + '」从当前父任务迁出吗？',
      impact: '迁出后该任务成为顶层任务，不再计入父任务的拆解树与甘特图；工时与排期数据保持不变，可继续单独管理。',
      confirmText: '确认迁出'
    }).then(function (ok) {
      if (ok && form) form.submit();
    });
  });

  /* ---------------- 拆解表单辅助 ---------------- */
  var form = document.querySelector('#decompose-form');
  if (form) {
    var prefillButton = document.querySelector('#prefill-window');
    if (prefillButton) {
      prefillButton.addEventListener('click', function () {
        var start = form.querySelector('input[name="start_at"]');
        var finish = form.querySelector('input[name="finish_at"]');
        if (start) start.value = prefillButton.dataset.start || '';
        if (finish) finish.value = prefillButton.dataset.finish || '';
        var title = form.querySelector('input[name="title"]');
        if (title) title.focus();
      });
    }

    /* 工时余额：输入预计工时时即时提示是否超出父任务剩余可分配 */
    var estimate = form.querySelector('input[name="estimate_minutes"]');
    var budget = document.querySelector('#budget-line');
    var usedNode = document.querySelector('#budget-used');
    var leftNode = document.querySelector('#budget-left');
    if (estimate && budget && CFG.remaining !== null && CFG.remaining !== undefined) {
      var update = function () {
        var value = parseInt(estimate.value, 10);
        if (isNaN(value) || value < 0) value = 0;
        var left = CFG.remaining - value;
        if (usedNode) usedNode.textContent = String(value);
        if (leftNode) leftNode.textContent = String(left);
        budget.classList.toggle('is-over', left < 0);
      };
      estimate.addEventListener('input', update);
      update();
    }
  }

  /* ---------------- Flash 提示自动收起 ---------------- */
  document.querySelectorAll('.notice').forEach(function (node) {
    window.setTimeout(function () {
      node.style.transition = 'opacity .4s';
      node.style.opacity = '0';
      window.setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 450);
    }, 6000);
  });
})();
