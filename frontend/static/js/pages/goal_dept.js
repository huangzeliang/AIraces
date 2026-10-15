/* 部门目标（A1 /goal/dept）页面交互：表单校验、权重合计、模板、AI 草案采纳、完整度检查 */
(function () {
  'use strict';

  var dataEl = document.getElementById('goal-dept-data');
  if (!dataEl) { return; }
  var DATA = JSON.parse(dataEl.textContent);
  var state = {
    objectiveId: DATA.objective ? DATA.objective.objective_id : '',
    originalParent: DATA.objective ? (DATA.objective.parent_id || '') : '',
    originalWeight: DATA.objective ? Number(DATA.objective.weight || 0) : 0,
    drafts: []
  };

  function $(id) { return document.getElementById(id); }

  function idemKey() {
    if (window.crypto && window.crypto.randomUUID) { return window.crypto.randomUUID(); }
    return 'k-' + Date.now() + '-' + Math.random().toString(16).slice(2);
  }

  function toast(message, isError) {
    var el = document.createElement('div');
    el.className = 'page-toast' + (isError ? ' is-error' : '');
    el.setAttribute('role', 'status');
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add('is-out'); }, 2600);
    setTimeout(function () { el.remove(); }, 3100);
  }

  function post(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idemKey() },
      body: JSON.stringify(payload)
    }).then(function (response) {
      return response.json().catch(function () { return { code: 1006, message: '服务响应异常，请重试。' }; });
    }).then(function (body) {
      if (body.code !== 0) {
        var error = new Error(body.message || '操作失败');
        error.code = body.code;
        error.data = body.data || {};
        throw error;
      }
      return body;
    });
  }

  /* ---------------- 成功标准编辑器 ---------------- */
  var criteriaList = $('criteria-list');

  function addCriteriaRow(value) {
    var row = document.createElement('div');
    row.className = 'criteria-row';
    var index = document.createElement('span');
    index.className = 'criteria-index';
    var input = document.createElement('input');
    input.maxLength = 500;
    input.placeholder = '例：11 月底完成 3 个核心模块上线；上线后 P1 缺陷保持 0';
    input.value = value || '';
    if (!DATA.can_write) { input.disabled = true; }
    var remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'mini-btn is-danger';
    remove.setAttribute('aria-label', '删除该条成功标准');
    remove.textContent = '✕';
    if (!DATA.can_write) { remove.disabled = true; }
    remove.addEventListener('click', function () {
      row.remove();
      if (!criteriaList.children.length) { addCriteriaRow(''); }
      renumber();
    });
    row.appendChild(index); row.appendChild(input); row.appendChild(remove);
    criteriaList.appendChild(row);
    renumber();
  }

  function renumber() {
    Array.prototype.forEach.call(criteriaList.querySelectorAll('.criteria-index'), function (el, i) {
      el.textContent = String(i + 1);
    });
  }

  function readCriteria() {
    return Array.prototype.map.call(criteriaList.querySelectorAll('input'), function (input) {
      return input.value.trim();
    }).filter(Boolean);
  }

  $('criteria-add').addEventListener('click', function () { addCriteriaRow(''); });

  var initialCriteria = (DATA.objective && DATA.objective.success_criteria) || [];
  if (initialCriteria.length) { initialCriteria.forEach(function (item) { addCriteriaRow(item); }); }
  else { addCriteriaRow(''); }

  /* ---------------- 标题计数 ---------------- */
  var titleInput = $('title-input');
  function updateTitleCount() { $('title-count').textContent = String(titleInput.value.length); }
  titleInput.addEventListener('input', updateTitleCount);
  updateTitleCount();

  /* ---------------- 权重实时合计 ---------------- */
  function selectedParent() { return $('parent-select').value; }

  function parentLabel(parentId) {
    var option = $('parent-select').querySelector('option[value="' + parentId + '"]');
    return option ? option.textContent.trim().replace(/^[　\s]+/, '') : '上级目标';
  }

  function updateWeightBar() {
    var bar = $('weight-bar');
    var parentId = selectedParent();
    var group = parentId ? (DATA.weight_ctx || {})[parentId] : null;
    if (!parentId || !group) { bar.hidden = true; return; }
    bar.hidden = false;
    var base = Number(group.sum || 0);
    if (state.objectiveId && state.originalParent === parentId) { base -= state.originalWeight; }
    var total = Math.round((base + readWeight()) * 10) / 10;
    $('weight-parent-name').textContent = parentLabel(parentId);
    $('weight-sum').textContent = total + '%';
    var warn = Math.abs(total - 100) > 0.01;
    bar.classList.toggle('is-warn', warn);
    $('weight-warn').hidden = !warn;
  }

  function readWeight() {
    var value = Number($('weight-input').value || 0);
    if (isNaN(value)) { return 0; }
    return Math.round(Math.min(100, Math.max(0, value)) * 10) / 10;
  }

  $('parent-select').addEventListener('change', updateWeightBar);
  $('weight-input').addEventListener('input', updateWeightBar);
  updateWeightBar();

  /* ---------------- 模板套用 ---------------- */
  $('apply-template').addEventListener('click', function () {
    var template = null;
    DATA.templates.forEach(function (item) {
      if (item.template_id === $('template-select').value) { template = item; }
    });
    if (!template) { return; }
    function apply() {
      titleInput.value = template.title;
      updateTitleCount();
      criteriaList.innerHTML = '';
      template.success_criteria.forEach(function (item) { addCriteriaRow(item); });
      toast('模板已套用，可继续编辑');
    }
    var hasContent = titleInput.value.trim() || readCriteria().length;
    if (hasContent) {
      window.NorthModal.confirm({
        title: '套用模板',
        message: '标题或成功标准已有内容，套用将覆盖当前输入。',
        confirmText: '覆盖套用'
      }).then(function (ok) { if (ok) { apply(); } });
    } else { apply(); }
  });

  /* ---------------- 表单校验与保存 ---------------- */
  function fieldError(id, message) {
    var el = $(id);
    if (!el) { return; }
    if (message) { el.textContent = message; el.hidden = false; }
    else { el.textContent = ''; el.hidden = true; }
  }

  function clearErrors() {
    ['title-error', 'source-error', 'parent-error', 'owner-error', 'start-error', 'end-error', 'criteria-error'].forEach(function (id) {
      fieldError(id, '');
    });
    var formError = $('form-error');
    formError.hidden = true;
    formError.textContent = '';
  }

  function collect() {
    return {
      objective_id: state.objectiveId,
      title: titleInput.value.trim(),
      source: $('source-input').value.trim(),
      parent_id: selectedParent(),
      org_unit_id: $('org-select').value,
      owner_id: $('owner-select').value,
      period_id: $('period-select').value,
      plan_start: $('start-input').value,
      plan_end: $('end-input').value,
      weight: readWeight(),
      success_criteria: readCriteria()
    };
  }

  function validate(payload) {
    var errors = [];
    if (!payload.title) { errors.push(['title-error', '目标标题不能为空。', 'title-input']); }
    else if (payload.title.length > 100) { errors.push(['title-error', '标题不能超过 100 个字符。', 'title-input']); }
    if (!payload.source) { errors.push(['source-error', '目标来源不能为空。', 'source-input']); }
    if (!payload.owner_id) { errors.push(['owner-error', '请选择唯一责任人（本部门在册成员）。', 'owner-select']); }
    if (!payload.success_criteria.length) { errors.push(['criteria-error', '成功标准至少 1 条。', null]); }
    if (!payload.plan_start) { errors.push(['start-error', '请选择计划开始日期。', 'start-input']); }
    if (!payload.plan_end) { errors.push(['end-error', '请选择计划结束日期。', 'end-input']); }
    if (payload.plan_start && payload.plan_end && payload.plan_end < payload.plan_start) {
      errors.push(['end-error', '结束日期不能早于开始日期。', 'end-input']);
    }
    var periodOption = $('period-select').selectedOptions[0];
    if (periodOption && payload.plan_start && payload.plan_end) {
      var start = periodOption.dataset.start, end = periodOption.dataset.end;
      if (start && payload.plan_start < start || end && payload.plan_end > end) {
        errors.push(['end-error', '计划起止须落在周期 ' + start + ' ~ ' + end + ' 内。', 'end-input']);
      }
    }
    var weight = Number($('weight-input').value);
    if (isNaN(weight) || weight < 0 || weight > 100) { errors.push([null, '权重必须是 0~100 的数字。', 'weight-input']); }
    return errors;
  }

  function saveDraft() {
    clearErrors();
    var payload = collect();
    var errors = validate(payload);
    if (errors.length) {
      var first = errors[0];
      if (first[0]) { fieldError(first[0], first[1]); }
      var formError = $('form-error');
      formError.textContent = first[1];
      formError.hidden = false;
      var focusTarget = first[2] ? $(first[2]) : null;
      if (focusTarget) { focusTarget.focus(); }
      return;
    }
    var button = $('save-button');
    button.disabled = true;
    var label = button.textContent;
    button.textContent = '保存中…';
    post(DATA.urls.save, payload).then(function (body) {
      var isNew = !state.objectiveId;
      state.objectiveId = body.data.objective_id;
      state.originalParent = payload.parent_id;
      state.originalWeight = payload.weight;
      $('objective-chip').textContent = body.data.objective_id;
      if (isNew) {
        window.location.href = window.location.pathname + '?objective_id=' + encodeURIComponent(body.data.objective_id);
        return;
      }
      var group = (DATA.weight_ctx || {})[payload.parent_id];
      if (group) {
        var found = null;
        group.children.forEach(function (child) { if (child.objective_id === state.objectiveId) { found = child; } });
        if (found) { found.weight = payload.weight; }
        else if (payload.parent_id) { group.children.push({ objective_id: state.objectiveId, title: payload.title, weight: payload.weight }); }
        group.sum = group.children.reduce(function (acc, child) { return acc + Number(child.weight || 0); }, 0);
      }
      updateWeightBar();
      toast(body.message || '草稿已保存');
    }).catch(function (error) {
      if (error.code === 1005 && error.data && error.data.field === 'parent_id') {
        fieldError('parent-error', error.message);
        $('parent-select').value = '';
        updateWeightBar();
      } else if (error.code === 1004) {
        var formError = $('form-error');
        formError.textContent = error.message + '（发布后字段锁定，请走「发起变更申请」）';
        formError.hidden = false;
      } else {
        var formError2 = $('form-error');
        formError2.textContent = error.message || '保存失败，请重试。';
        formError2.hidden = false;
      }
      toast(error.message || '保存失败，请重试。', true);
    }).then(function () {
      button.disabled = false;
      button.textContent = label;
    });
  }

  $('objective-form').addEventListener('submit', function (event) {
    event.preventDefault();
    if (!DATA.can_write) { return; }
    saveDraft();
  });

  /* ---------------- AI 拆解建议（草案不落库） ---------------- */
  var draftList = $('draft-list');
  var adoptButton = $('adopt-button');

  function renderDraftEmpty(message) {
    draftList.innerHTML = '';
    var li = document.createElement('li');
    li.className = 'draft-empty';
    li.textContent = message;
    draftList.appendChild(li);
    adoptButton.disabled = true;
  }

  function updateAdoptState() {
    var checked = draftList.querySelectorAll('input[type="checkbox"]:checked').length;
    adoptButton.disabled = !checked || !DATA.can_write;
  }

  function renderDrafts(children, engine, degraded) {
    state.drafts = children;
    draftList.innerHTML = '';
    children.forEach(function (child, index) {
      var li = document.createElement('li');
      li.className = 'draft-item';
      var head = document.createElement('div');
      head.className = 'draft-head';
      var checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.id = 'draft-' + index;
      checkbox.addEventListener('change', function () {
        li.classList.toggle('is-checked', checkbox.checked);
        updateAdoptState();
      });
      var label = document.createElement('label');
      label.className = 'draft-title';
      label.setAttribute('for', 'draft-' + index);
      label.textContent = child.title;
      head.appendChild(checkbox); head.appendChild(label);
      var criteria = document.createElement('ul');
      criteria.className = 'draft-criteria';
      (child.success_criteria || []).forEach(function (item) {
        var row = document.createElement('li');
        row.textContent = '· ' + item;
        criteria.appendChild(row);
      });
      var rationale = document.createElement('p');
      rationale.className = 'draft-rationale';
      rationale.textContent = '依据：' + (child.rationale || '—');
      var ignore = document.createElement('button');
      ignore.type = 'button';
      ignore.className = 'draft-ignore';
      ignore.textContent = '忽略此条';
      ignore.addEventListener('click', function () {
        state.drafts[index] = null;
        li.remove();
        updateAdoptState();
      });
      li.appendChild(head); li.appendChild(criteria); li.appendChild(rationale); li.appendChild(ignore);
      draftList.appendChild(li);
    });
    var engineEl = $('ai-engine');
    engineEl.hidden = false;
    engineEl.textContent = '生成引擎：' + engine + (degraded ? '（已降级为规则估算）' : '');
    updateAdoptState();
  }

  $('ai-decompose-button').addEventListener('click', function () {
    if (!DATA.can_write) { return; }
    if (!state.objectiveId) { toast('请先保存草稿，再生成拆解建议。', true); return; }
    var button = this;
    button.disabled = true;
    button.textContent = '生成中…';
    post(DATA.urls.decompose, { objective_id: state.objectiveId }).then(function (body) {
      renderDrafts(body.data.children || [], body.data.engine || '', !!body.data.degraded);
      $('ai-hint').textContent = body.data.notice || '';
    }).catch(function (error) {
      renderDraftEmpty('建议生成失败，可手动录入子目标。(' + (error.message || '服务异常') + ')');
    }).then(function () {
      button.disabled = false;
      button.textContent = '生成拆解草案';
    });
  });

  adoptButton.addEventListener('click', function () {
    var checked = [];
    Array.prototype.forEach.call(draftList.querySelectorAll('input[type="checkbox"]:checked'), function (box) {
      var index = Number(box.id.replace('draft-', ''));
      if (state.drafts[index]) { checked.push(state.drafts[index]); }
    });
    if (!checked.length) { return; }
    var base = collect();
    if (!base.owner_id || !base.plan_start || !base.plan_end) {
      toast('采纳草案需要先选择责任人 / 计划时间窗（使用当前表单取值）。', true);
      return;
    }
    adoptButton.disabled = true;
    var tasks = checked.map(function (child) {
      return post(DATA.urls.save, {
        title: child.title,
        source: 'AI 拆解采纳',
        parent_id: state.objectiveId,
        org_unit_id: base.org_unit_id,
        owner_id: base.owner_id,
        period_id: base.period_id,
        plan_start: base.plan_start,
        plan_end: base.plan_end,
        weight: 0,
        success_criteria: child.success_criteria
      });
    });
    Promise.all(tasks).then(function () {
      toast('已采纳 ' + checked.length + ' 条草案为子目标草稿，正在刷新…');
      setTimeout(function () { window.location.reload(); }, 600);
    }).catch(function (error) {
      toast('采纳失败：' + (error.message || '请重试'), true);
      adoptButton.disabled = false;
    });
  });

  /* ---------------- 拆解完整度检查（本地比对，不落库） ---------------- */
  function bigrams(text) {
    var clean = String(text || '').replace(/[^\u4e00-\u9fa5A-Za-z0-9]/g, '');
    var set = {};
    for (var i = 0; i < clean.length - 1; i++) { set[clean.slice(i, i + 2)] = true; }
    return set;
  }

  function coverage(clause, text) {
    var a = bigrams(clause);
    var keys = Object.keys(a);
    if (!keys.length) { return 0; }
    var b = bigrams(text);
    var hit = 0;
    keys.forEach(function (key) { if (b[key]) { hit++; } });
    return hit / keys.length;
  }

  function jaccard(a, b) {
    var setA = bigrams(a), setB = bigrams(b);
    var keysA = Object.keys(setA), keysB = Object.keys(setB);
    if (!keysA.length || !keysB.length) { return 0; }
    var hit = 0;
    keysA.forEach(function (key) { if (setB[key]) { hit++; } });
    var union = keysA.length + keysB.length - hit;
    return union ? hit / union : 0;
  }

  $('completeness-button').addEventListener('click', function () {
    var container = $('completeness-result');
    container.innerHTML = '';
    if (!state.objectiveId) {
      container.innerHTML = '<p class="draft-empty">请先保存草稿，再执行完整度检查。</p>';
      return;
    }
    var criteria = readCriteria();
    var children = (DATA.children_ctx || {})[state.objectiveId] || [];
    if (!criteria.length) {
      container.innerHTML = '<p class="draft-empty">请先填写成功标准。</p>';
      return;
    }
    if (!children.length) {
      container.innerHTML = '<p class="draft-empty">当前目标还没有子目标：可先用「AI 拆解建议」生成草案并采纳，或到拆解树手动下拆。</p>';
      return;
    }
    var missing = 0, ok = 0;
    criteria.forEach(function (clause) {
      var matched = null;
      children.forEach(function (child) {
        var text = (child.title || '') + ' ' + (child.criteria || []).join(' ');
        if (coverage(clause, text) >= 0.5) { matched = child; }
      });
      var line = document.createElement('div');
      if (matched) {
        ok++;
        line.className = 'completeness-line ok';
        line.innerHTML = '<b>✓ 已覆盖</b>';
        var span = document.createElement('span');
        span.textContent = '「' + clause + '」 ← ' + matched.title;
        line.appendChild(span);
      } else {
        missing++;
        line.className = 'completeness-line missing';
        line.innerHTML = '<b>⚠ 疑似漏项</b>';
        var span2 = document.createElement('span');
        span2.textContent = '「' + clause + '」暂无子目标覆盖';
        line.appendChild(span2);
      }
      container.appendChild(line);
    });
    var overlaps = 0;
    children.forEach(function (child, i) {
      children.slice(i + 1).forEach(function (other) {
        var textA = (child.title || '') + ' ' + (child.criteria || []).join(' ');
        var textB = (other.title || '') + ' ' + (other.criteria || []).join(' ');
        if (jaccard(textA, textB) >= 0.55) {
          overlaps++;
          var line = document.createElement('div');
          line.className = 'completeness-line overlap';
          line.innerHTML = '<b>⚠ 疑似重叠</b>';
          var span = document.createElement('span');
          span.textContent = '「' + child.title + '」与「' + other.title + '」语义高度相近，建议合并或区分边界';
          line.appendChild(span);
          container.appendChild(line);
        }
      });
    });
    var summary = document.createElement('p');
    summary.className = 'field-hint';
    summary.textContent = '比对结果：覆盖 ' + ok + ' 条 · 漏项 ' + missing + ' 条 · 疑似重叠 ' + overlaps + ' 对（本地启发式比对，仅提示不阻断）。';
    container.appendChild(summary);
  });

})();
