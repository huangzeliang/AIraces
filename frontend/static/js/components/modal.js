/* 公共模态框组件（B4）：NorthModal
   - 打开时才创建 .modal-overlay > .modal[role=dialog]，关闭立即从 DOM 移除：
     页面处于关闭态时不残留任何 .modal 节点（隐藏元素会干扰自动化断言）；
   - NorthModal.confirm(...) 返回 Promise<boolean>，取消 / Esc / 点遮罩都结算为 false；
   - Esc 关闭、点遮罩关闭、打开聚焦首个操作元素、关闭还原焦点、背景禁止滚动。 */
(function () {
  'use strict';

  var current = null;

  function onKeydown(event) {
    if (event.key === 'Escape' && current) {
      event.stopPropagation();
      current.close();
    }
  }

  function open(options) {
    options = options || {};
    if (current) current.close();

    var lastFocus = document.activeElement;
    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    var modal = document.createElement('div');
    modal.className = 'modal' + (options.danger ? ' is-danger' : '');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    if (options.title) modal.setAttribute('aria-label', options.title);

    var head = document.createElement('div');
    head.className = 'modal-head';
    var heading = document.createElement('h3');
    heading.textContent = options.title || '提示';
    head.appendChild(heading);
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'modal-close';
    closeBtn.setAttribute('aria-label', '关闭');
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', function () { close(); });
    head.appendChild(closeBtn);
    modal.appendChild(head);

    var body = document.createElement('div');
    body.className = 'modal-body';
    if (options.message) {
      var message = document.createElement('p');
      message.className = 'modal-message';
      message.textContent = options.message;
      body.appendChild(message);
    }
    if (options.impact) {
      var impact = document.createElement('p');
      impact.className = 'modal-impact';
      impact.textContent = options.impact;
      body.appendChild(impact);
    }
    if (options.content) body.appendChild(options.content);
    modal.appendChild(body);

    var foot = document.createElement('div');
    foot.className = 'modal-foot';
    var cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'button button-secondary';
    cancelBtn.textContent = options.cancelText || '取消';
    var confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'button ' + (options.danger ? 'button-danger' : 'button-primary');
    confirmBtn.textContent = options.confirmText || '确认';
    foot.appendChild(cancelBtn);
    foot.appendChild(confirmBtn);
    modal.appendChild(foot);

    overlay.appendChild(modal);

    function close() {
      document.removeEventListener('keydown', onKeydown, true);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      document.body.classList.remove('modal-open');
      if (current && current.overlay === overlay) current = null;
      if (lastFocus && document.contains(lastFocus) && lastFocus.focus) lastFocus.focus();
      if (options.onClose) options.onClose();
    }

    cancelBtn.addEventListener('click', function () { close(); });
    confirmBtn.addEventListener('click', function () {
      var result = options.onConfirm ? options.onConfirm() : true;
      if (result === false) return;
      if (result && typeof result.then === 'function') {
        result.then(function (ok) { if (ok !== false) close(); });
        return;
      }
      close();
    });
    overlay.addEventListener('mousedown', function (event) {
      if (event.target === overlay) close();
    });

    document.body.appendChild(overlay);
    document.body.classList.add('modal-open');
    document.addEventListener('keydown', onKeydown, true);

    var autoFocus = options.autoFocus ? modal.querySelector(options.autoFocus) : null;
    (autoFocus || confirmBtn).focus();

    current = { overlay: overlay, modal: modal, body: body, close: close, confirm: confirmBtn, cancel: cancelBtn };
    return current;
  }

  function confirm(options) {
    options = options || {};
    var settled = false;
    return new Promise(function (resolve) {
      function finish(value) {
        if (settled) return;
        settled = true;
        resolve(value);
      }
      open({
        title: options.title || '请确认',
        message: options.message,
        impact: options.impact,
        content: options.content,
        confirmText: options.confirmText || '确认',
        cancelText: options.cancelText || '取消',
        danger: !!options.danger,
        autoFocus: options.autoFocus,
        onConfirm: function () { finish(true); return true; },
        onClose: function () { finish(false); }
      });
    });
  }

  function close() {
    if (current) current.close();
  }

  window.NorthModal = { open: open, confirm: confirm, close: close };
})();
