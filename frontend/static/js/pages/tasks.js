/* 任务列表（B1）页面脚本：筛选自动提交、进度滑杆、完成/延期/取消/编辑/删除、撤销提示条。
   依赖：components/modal.js 提供的 window.NorthModal（B4 公共组件）。零依赖其余实现。 */

(function () {
  'use strict';

  var configNode = document.querySelector('#tasks-config');
  var CFG = { urls: {}, next: '', plans: [], owners: [] };
  if (configNode) {
    try { CFG = JSON.parse(configNode.textContent); } catch (error) { /* 保持默认值 */ }
  }
  var URLS = CFG.urls || {};
  var NEXT = CFG.next || '';

  function ownerOptions(current, emptyLabel) {
    var names = [];
    if (current) names.push(current);
    (CFG.owners || []).forEach(function (name) {
      if (names.indexOf(name) === -1) names.push(name);
    });
    return [{ value: '', label: emptyLabel }].concat(names.map(function (name) {
      return { value: name, label: name };
    }));
  }

  function url(key, id) {
    return (URLS[key] || '').replace('__ID__', id);
  }

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

  function toast(message, isError, undoUrl) {
    var stack = ensureStack();
    var node = document.createElement('div');
    node.className = 'toast' + (isError ? ' is-error' : '');
    var text = document.createElement('span');
    text.textContent = message;
    node.appendChild(text);
    if (undoUrl) {
      var form = document.createElement('form');
      form.className = 'toast-undo';
      form.method = 'post';
      form.action = undoUrl;
      var hidden = document.createElement('input');
      hidden.type = 'hidden';
      hidden.name = 'next';
      hidden.value = NEXT;
      form.appendChild(hidden);
      var button = document.createElement('button');
      button.type = 'submit';
      button.className = 'toast-btn';
      button.textContent = '撤销';
      form.appendChild(button);
      node.appendChild(form);
    }
    stack.appendChild(node);
    window.setTimeout(function () {
      node.classList.add('is-out');
      window.setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 420);
    }, undoUrl ? 9000 : 5200);
  }

  /* ---------------- 表单构造 ---------------- */
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

  function makeForm(action) {
    var form = document.createElement('form');
    form.method = 'post';
    form.action = action;
    form.noValidate = true;
    var hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = 'next';
    hidden.value = NEXT;
    form.appendChild(hidden);
    return form;
  }

  function sendForm(form) {
    if (typeof form.requestSubmit === 'function') form.requestSubmit();
    else form.submit();
  }

  function makeField(spec) {
    var wrap = document.createElement('label');
    wrap.className = 'modal-field';
    var caption = document.createElement('span');
    caption.textContent = spec.label;
    wrap.appendChild(caption);
    var input;
    if (spec.tag === 'textarea') {
      input = document.createElement('textarea');
      input.rows = 3;
    } else if (spec.tag === 'select') {
      input = document.createElement('select');
      (spec.options || []).forEach(function (option) {
        var node = document.createElement('option');
        node.value = option.value;
        node.textContent = option.label;
        input.appendChild(node);
      });
    } else {
      input = document.createElement('input');
      input.type = spec.type || 'text';
    }
    if (spec.name) input.name = spec.name;
    if (spec.value !== undefined && spec.value !== null) input.value = spec.value;
    if (spec.maxlength) input.maxLength = spec.maxlength;
    if (spec.min !== undefined) input.min = spec.min;
    if (spec.placeholder) input.placeholder = spec.placeholder;
    wrap.appendChild(input);
    return { node: wrap, input: input };
  }

  function makeError() {
    var error = document.createElement('p');
    error.className = 'modal-error';
    error.hidden = true;
    return error;
  }

  function showError(error, message) {
    error.textContent = message;
    error.hidden = false;
  }

  /* ---------------- 筛选自动提交（URL 保留筛选态） ---------------- */
  var filterForm = document.querySelector('#task-filter');
  if (filterForm) {
    filterForm.querySelectorAll('input[type="checkbox"], input[type="date"]').forEach(function (input) {
      input.addEventListener('change', function () { filterForm.submit(); });
    });
    var keyword = filterForm.querySelector('input[name="keyword"]');
    if (keyword) {
      var keywordTimer = null;
      var composing = false;
      var queueSubmit = function () {
        window.clearTimeout(keywordTimer);
        keywordTimer = window.setTimeout(function () { filterForm.submit(); }, 250);
      };
      /* 中文输入法组字期间（拼音未上屏）不触发提交，避免页面刷新打断选词 */
      keyword.addEventListener('compositionstart', function () {
        composing = true;
        window.clearTimeout(keywordTimer);
      });
      keyword.addEventListener('compositionend', function () {
        composing = false;
        queueSubmit();
      });
      keyword.addEventListener('input', function (event) {
        if (composing || event.isComposing) return;
        queueSubmit();
      });
      keyword.addEventListener('change', function () {
        window.clearTimeout(keywordTimer);
        filterForm.submit();
      });
      keyword.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter') return;
        /* 组字中的 Enter/Space 是输入法选词键，不能当作确认查询 */
        if (composing || event.isComposing || event.keyCode === 229) return;
        event.preventDefault();
        window.clearTimeout(keywordTimer);
        filterForm.submit();
      });
    }
  }

  /* ---------------- 行内状态刷新 ---------------- */
  function applyStatus(row, data) {
    var tag = row.querySelector('[data-role="status"]');
    if (!tag || !data.status) return;
    tag.className = 'status-tag status-' + (data.status_key || 'ready');
    tag.textContent = '';
    var dot = document.createElement('span');
    dot.className = 'dot';
    tag.appendChild(dot);
    tag.appendChild(document.createTextNode(data.status));
  }

  function hideRowActions(row) {
    ['complete', 'delay', 'cancel'].forEach(function (action) {
      var button = row.querySelector('[data-action="' + action + '"]');
      if (button) button.style.display = 'none';
    });
  }

  function applyProgress(row, slider, data) {
    var output = row.querySelector('[data-role="progress-value"]');
    var fill = row.querySelector('[data-role="progress-fill"]');
    if (output) output.textContent = data.progress + '%';
    if (fill) fill.style.width = data.progress + '%';
    applyStatus(row, data);
    if (data.completed) {
      row.classList.add('is-done');
      slider.disabled = true;
      hideRowActions(row);
      toast('进度已达 100%，任务自动完成。', false,
        data.feedback_id ? url('undo', data.feedback_id) : '');
    }
  }

  /* ---------------- 进度滑杆：即时反馈 + 去抖保存 ---------------- */
  var sliderTimers = {};

  function previewSlider(slider) {
    var row = document.getElementById('task-' + slider.dataset.task);
    if (!row) return;
    var output = row.querySelector('[data-role="progress-value"]');
    var fill = row.querySelector('[data-role="progress-fill"]');
    var value = parseInt(slider.value, 10) || 0;
    if (output) output.textContent = value + '%';
    if (fill) fill.style.width = value + '%';
  }

  function sendProgress(slider) {
    var value = parseInt(slider.value, 10) || 0;
    if (String(value) === slider.dataset.sent) return;
    slider.dataset.sent = String(value);
    var row = document.getElementById('task-' + slider.dataset.task);
    fetch(url('progress', slider.dataset.task), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({ progress: value })
    }).then(function (response) { return response.json(); }).then(function (data) {
      if (!data.ok) {
        slider.dataset.sent = '';
        toast(data.message || '进度保存失败，请重试。', true);
        return;
      }
      if (row) applyProgress(row, slider, data);
    }).catch(function () {
      slider.dataset.sent = '';
      toast('网络异常，进度未保存，请重试。', true);
    });
  }

  document.addEventListener('input', function (event) {
    var slider = event.target;
    if (!slider.classList || !slider.classList.contains('progress-slider')) return;
    previewSlider(slider);
    var key = slider.dataset.task;
    window.clearTimeout(sliderTimers[key]);
    sliderTimers[key] = window.setTimeout(function () { sendProgress(slider); }, 250);
  });

  document.addEventListener('change', function (event) {
    var slider = event.target;
    if (!slider.classList || !slider.classList.contains('progress-slider')) return;
    var key = slider.dataset.task;
    window.clearTimeout(sliderTimers[key]);
    previewSlider(slider);
    sendProgress(slider);
  });

  /* ---------------- 完成（直接反馈 + 撤销提示条） ---------------- */
  function completeTask(button) {
    var id = button.dataset.task;
    button.disabled = true;
    fetch(url('complete', id), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({})
    }).then(function (response) { return response.json(); }).then(function (data) {
      if (!data.ok) {
        button.disabled = false;
        toast(data.message || '操作失败，请重试。', true);
        return;
      }
      var row = document.getElementById('task-' + id);
      if (row) {
        applyStatus(row, data);
        var slider = row.querySelector('.progress-slider');
        var fill = row.querySelector('[data-role="progress-fill"]');
        var output = row.querySelector('[data-role="progress-value"]');
        if (slider) { slider.value = 100; slider.disabled = true; }
        if (fill) fill.style.width = '100%';
        if (output) output.textContent = '100%';
        row.classList.add('is-done');
        hideRowActions(row);
      }
      toast(data.message, false, data.feedback_id ? url('undo', data.feedback_id) : '');
    }).catch(function () {
      button.disabled = false;
      toast('网络异常，操作未保存，请重试。', true);
    });
  }

  /* ---------------- 延期（原因必填弹窗） ---------------- */
  function openDelayModal(button) {
    var id = button.dataset.task;
    var title = button.dataset.title || id;
    var form = makeForm(url('delay', id));
    var reason = makeField({
      label: '延期原因（必填，≤200 字）',
      tag: 'textarea',
      name: 'delay_reason',
      maxlength: 200,
      placeholder: '例如：上游数据口径未对齐，等待确认后重新统计'
    });
    form.appendChild(reason.node);
    var error = makeError();
    form.appendChild(error);

    var dialog = window.NorthModal.open({
      title: '延期任务',
      message: '「' + title + '」尚未完成，请先写明延期原因；提交后会写入一条延期反馈，可在提示条里撤销。',
      content: form,
      confirmText: '确认延期',
      autoFocus: 'textarea[name="delay_reason"]',
      onConfirm: function () {
        var value = reason.input.value.trim();
        if (!value) {
          showError(error, '延期原因不能为空（必填）。');
          reason.input.focus();
          return false;
        }
        if (value.length > 200) {
          showError(error, '延期原因不能超过 200 个字符。');
          return false;
        }
        dialog.confirm.disabled = true;
        sendForm(form);
        return false;
      }
    });
  }

  /* ---------------- 取消 / 删除（二次确认） ---------------- */
  function cancelTask(button) {
    var id = button.dataset.task;
    var title = button.dataset.title || id;
    window.NorthModal.confirm({
      title: '取消任务',
      message: '确定取消「' + title + '」吗？',
      impact: '取消后任务不再计入未完成列表，系统会写入一条取消反馈；如需恢复可在撤销或编辑中处理。',
      confirmText: '确认取消'
    }).then(function (ok) {
      if (ok) submitHidden(url('cancel', id));
    });
  }

  function deleteTask(button) {
    var id = button.dataset.task;
    var title = button.dataset.title || id;
    window.NorthModal.confirm({
      title: '删除任务',
      message: '确定删除「' + title + '」吗？',
      impact: '删除不可恢复：任务会写入状态日志后移除；历史反馈与提醒会保留，对应提醒将标注「任务不存在」。',
      confirmText: '确认删除',
      danger: true
    }).then(function (ok) {
      if (ok) submitHidden(url('delete', id));
    });
  }

  /* ---------------- 编辑弹窗 ---------------- */
  function openEditModal(button) {
    var id = button.dataset.task;
    var form = makeForm(url('edit', id));
    var priority = makeField({
      label: '优先级', tag: 'select', name: 'priority', value: button.dataset.priority || '中',
      options: [{ value: '高', label: '高' }, { value: '中', label: '中' }, { value: '低', label: '低' }]
    });
    var estimate = makeField({ label: '预计耗时（分钟）', name: 'estimate_minutes', type: 'number', value: button.dataset.estimate || '60', min: 1 });
    var grid = document.createElement('div');
    grid.className = 'modal-grid';
    grid.appendChild(priority.node);
    grid.appendChild(estimate.node);

    var title = makeField({ label: '任务标题（必填，≤80 字）', name: 'title', value: button.dataset.title || '', maxlength: 80 });
    var detail = makeField({ label: '任务描述（选填，≤500 字）', tag: 'textarea', name: 'detail', value: button.dataset.detail || '', maxlength: 500 });
    var owner = makeField({
      label: '负责人（调整归属）', tag: 'select', name: 'owner',
      value: button.dataset.owner || '', options: ownerOptions(button.dataset.owner || '', '不指派')
    });
    var due = makeField({ label: '截止时间（选填）', name: 'due_at', type: 'datetime-local', value: button.dataset.due || '' });

    var grid2 = document.createElement('div');
    grid2.className = 'modal-grid';
    grid2.appendChild(owner.node);
    grid2.appendChild(due.node);

    form.appendChild(title.node);
    form.appendChild(detail.node);
    form.appendChild(grid);
    form.appendChild(grid2);
    var error = makeError();
    form.appendChild(error);

    var dialog = window.NorthModal.open({
      title: '编辑任务',
      message: '任务 ' + id + '：修改保存后立即写入存储，并记录一条编辑日志。',
      content: form,
      confirmText: '保存修改',
      autoFocus: 'input[name="title"]',
      onConfirm: function () {
        var value = title.input.value.trim();
        if (!value) {
          showError(error, '任务标题不能为空。');
          title.input.focus();
          return false;
        }
        if (value.length > 80) {
          showError(error, '任务标题不能超过 80 个字符。');
          return false;
        }
        var minutes = parseInt(estimate.input.value, 10);
        if (!minutes || minutes <= 0) {
          showError(error, '预计耗时必须为大于 0 的整数。');
          estimate.input.focus();
          return false;
        }
        dialog.confirm.disabled = true;
        sendForm(form);
        return false;
      }
    });
  }

  /* ---------------- 新建任务弹窗 ---------------- */
  function openCreateModal() {
    var form = makeForm(URLS.create || '');
    var title = makeField({ label: '任务标题（必填，≤80 字）', name: 'title', maxlength: 80, placeholder: '例如：整理客户反馈清单' });
    var detail = makeField({ label: '任务描述（选填，≤500 字）', tag: 'textarea', name: 'detail', maxlength: 500 });
    var priority = makeField({
      label: '优先级', tag: 'select', name: 'priority', value: '中',
      options: [{ value: '高', label: '高' }, { value: '中', label: '中' }, { value: '低', label: '低' }]
    });
    var estimate = makeField({ label: '预计耗时（分钟）', name: 'estimate_minutes', type: 'number', value: '60', min: 1 });
    var planOptions = [{ value: '', label: '不关联计划' }].concat((CFG.plans || []).map(function (plan) {
      return { value: plan.plan_id, label: plan.plan_id + ' · ' + plan.goal };
    }));
    var plan = makeField({ label: '所属计划（选填）', tag: 'select', name: 'plan_id', options: planOptions });
    var owner = makeField({ label: '负责人（选填，拆解到人）', tag: 'select', name: 'owner', options: ownerOptions('', '暂不指派') });
    var due = makeField({ label: '截止时间（选填）', name: 'due_at', type: 'datetime-local' });

    var grid = document.createElement('div');
    grid.className = 'modal-grid';
    grid.appendChild(priority.node);
    grid.appendChild(estimate.node);

    form.appendChild(title.node);
    form.appendChild(detail.node);
    form.appendChild(grid);
    form.appendChild(plan.node);
    form.appendChild(owner.node);
    form.appendChild(due.node);
    var error = makeError();
    form.appendChild(error);

    var dialog = window.NorthModal.open({
      title: '新建任务',
      message: '新建任务默认为「待安排」，保存后可到智能排期生成安排。',
      content: form,
      confirmText: '创建任务',
      autoFocus: 'input[name="title"]',
      onConfirm: function () {
        var value = title.input.value.trim();
        if (!value) {
          showError(error, '任务标题不能为空。');
          title.input.focus();
          return false;
        }
        var minutes = parseInt(estimate.input.value, 10);
        if (!minutes || minutes <= 0) {
          showError(error, '预计耗时必须为大于 0 的整数。');
          estimate.input.focus();
          return false;
        }
        dialog.confirm.disabled = true;
        sendForm(form);
        return false;
      }
    });
  }

  /* ---------------- 事件委托 ---------------- */
  document.addEventListener('click', function (event) {
    var button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.open === 'new-task') {
      openCreateModal();
      return;
    }
    var action = button.dataset.action;
    if (!action) return;
    if (action === 'complete') completeTask(button);
    else if (action === 'delay') openDelayModal(button);
    else if (action === 'cancel') cancelTask(button);
    else if (action === 'delete') deleteTask(button);
    else if (action === 'edit') openEditModal(button);
  });

  /* ---------------- 提醒中心跳转过来的任务：定位并高亮 ---------------- */
  var focusRow = document.querySelector('.task-row.is-focus');
  if (focusRow) {
    try { focusRow.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (error) { focusRow.scrollIntoView(); }
    focusRow.classList.add('is-pulse');
  }

  /* ---------------- Flash 提示自动收起 ---------------- */
  document.querySelectorAll('.notice').forEach(function (node) {
    window.setTimeout(function () {
      node.classList.add('is-out');
      window.setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 450);
    }, 6000);
  });
})();
