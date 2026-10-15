/* C 线 · 结构化复盘页面的交互：AI 初稿、改进项增删与转化 */
(function () {
  'use strict';

  var payload = {};
  var dataNode = document.getElementById('retro-data');
  if (dataNode) {
    try { payload = JSON.parse(dataNode.textContent); } catch (error) { payload = {}; }
  }
  if (!payload.can_edit) { return; }

  /* ---------------- AI 初稿：只填空白框 ---------------- */
  var draftBtn = document.getElementById('draft-btn');
  if (draftBtn) {
    draftBtn.addEventListener('click', function () {
      draftBtn.disabled = true;
      draftBtn.textContent = '生成中…';
      fetch('/retro/' + encodeURIComponent(payload.objective_id) + '/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          var filled = 0;
          Object.keys(data.sections || {}).forEach(function (label) {
            var target = document.querySelector('textarea[data-section="' + label + '"]');
            if (target && !target.value.trim()) {
              target.value = data.sections[label];
              filled += 1;
              target.classList.add('is-drafted');
            }
          });
          draftBtn.disabled = false;
          draftBtn.textContent = '重新生成初稿';
          draftBtn.setAttribute('aria-expanded', 'true');
          announce(filled
            ? '已填入 ' + filled + ' 段初稿（来源：' + (data.source === 'llm' ? '大模型' : '规则引擎') + '），已写内容未被覆盖。'
            : '各段都已有内容，未做覆盖。');
        })
        .catch(function () {
          draftBtn.disabled = false;
          draftBtn.textContent = 'AI 生成初稿';
          announce('初稿生成失败，请稍后重试。');
        });
    });
  }

  /* ---------------- 添加改进项 ---------------- */
  var addBtn = document.getElementById('improve-add');
  var list = document.getElementById('improve-list');
  var template = document.getElementById('improve-template');
  if (addBtn && list && template) {
    addBtn.addEventListener('click', function () {
      var empty = document.getElementById('improve-empty');
      if (empty) { empty.remove(); }
      list.appendChild(template.content.cloneNode(true));
      var rows = list.querySelectorAll('.improve-row');
      var last = rows[rows.length - 1];
      if (last) { last.querySelector('.improve-text').focus(); }
    });
  }

  if (list) {
    list.addEventListener('click', function (event) {
      var removeBtn = event.target.closest('.improve-remove');
      if (removeBtn) {
        var row = removeBtn.closest('.improve-row');
        var index = row.getAttribute('data-index');
        if (index !== null) {
          var deleteIndex = document.getElementById('delete-index');
          if (deleteIndex) {
            deleteIndex.value = index;
            document.getElementById('delete-improve-form').submit();
            return;
          }
        }
        row.remove();
        if (!list.querySelector('.improve-row')) {
          list.innerHTML = '<p class="empty-block" id="improve-empty">还没有改进项，点「添加改进项」开始沉淀。</p>';
        }
        return;
      }

      var convertBtn = event.target.closest('.convert-btn');
      if (convertBtn) {
        var row = convertBtn.closest('.improve-row');
        var input = row.querySelector('.improve-text');
        if (input && !input.value.trim()) {
          announce('请先填写改进项内容再转化。');
          input.focus();
          return;
        }
        var hidden = document.getElementById('convert-index');
        hidden.value = convertBtn.getAttribute('data-index');
        document.getElementById('convert-form').submit();
      }
    });
  }

  function announce(text) {
    var node = document.getElementById('retro-flash');
    if (!node) {
      node = document.createElement('div');
      node.id = 'retro-flash';
      node.className = 'notice ok';
      node.setAttribute('role', 'status');
      var panel = document.querySelector('#retro-form .panel');
      if (panel && panel.parentNode) { panel.parentNode.insertBefore(node, panel); }
    }
    node.textContent = text;
  }
})();
