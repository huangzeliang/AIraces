/* SMART 校验（A2 /goal/smart）页面交互：五维环形、建议采纳、会签操作 */
(function () {
  'use strict';

  var dataEl = document.getElementById('goal-smart-data');
  if (!dataEl) { return; }
  var DATA = JSON.parse(dataEl.textContent);
  var NS = 'http://www.w3.org/2000/svg';
  var DIM_ORDER = ['S', 'M', 'A', 'R', 'T'];
  var META = {};
  (DATA.dims || []).forEach(function (dim) { META[dim.key] = { label: dim.label, target_field: dim.target_field }; });

  var state = {
    hasSmart: !!DATA.has_smart,
    block: DATA.block_reason || '',
    dims: []
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

  /* ---------------- 数据 -> 视图 ---------------- */
  function totalScore() {
    return state.dims.reduce(function (sum, dim) { return sum + dim.score; }, 0);
  }

  function anyLow() {
    return state.dims.some(function (dim) { return dim.low; });
  }

  function applySmart(smart, blockReason) {
    state.hasSmart = !!smart && Object.keys(smart).length > 0;
    state.block = blockReason || '';
    state.dims = DIM_ORDER.map(function (key) {
      var row = (smart && smart[key]) || {};
      var score = Number(row.score || 0);
      return {
        key: key,
        label: (META[key] && META[key].label) || key,
        score: score,
        comment: row.comment || '',
        suggestion: row.suggestion || '',
        target_field: (META[key] && META[key].target_field) || '',
        low: false
      };
    });
    state.dims.forEach(function (dim) { dim.low = state.hasSmart && dim.score < DATA.min_score; });
  }

  function applySmartMeta(meta) {
    if (!meta) { return; }
    var chip = $('engine-chip');
    if (!chip) { return; }
    var at = (meta.checked_at || '').replace('T', ' ').slice(0, 16);
    chip.textContent = '质检引擎：' + meta.engine + (meta.degraded ? '（规则兜底）' : '') + (at ? ' · ' + at : '');
    chip.hidden = false;
  }

  function svgEl(name, attrs) {
    var el = document.createElementNS(NS, name);
    for (var key in attrs) { el.setAttribute(key, attrs[key]); }
    return el;
  }

  function scoreColor(score) {
    if (score < DATA.min_score) { return 'var(--danger)'; }
    if (score >= 17) { return 'var(--green)'; }
    return 'var(--accent)';
  }

  function renderRing() {
    var box = $('ring-wrap');
    if (!box) { return; }
    box.textContent = '';
    var total = totalScore();
    var svg = svgEl('svg', {
      viewBox: '0 0 140 140', 'class': 'smart-ring', role: 'img',
      'aria-label': state.hasSmart ? 'SMART 五维评分环形图，总分 ' + total + ' / 100' : 'SMART 尚未质检'
    });
    var R = 56, C = 2 * Math.PI * R, seg = C / 5, gap = 10, arc = seg - gap;

    state.dims.forEach(function (dim, index) {
      var offset = -(index * seg + gap / 2);
      svg.appendChild(svgEl('circle', {
        cx: 70, cy: 70, r: R, fill: 'none', 'stroke-width': 11,
        'stroke-dasharray': arc + ' ' + C, 'stroke-dashoffset': offset,
        transform: 'rotate(-90 70 70)', 'class': 'ring-track'
      }));
      var ratio = state.hasSmart ? dim.score / 20 : 0;
      var len = ratio > 0 ? Math.max(3, arc * ratio) : 0;
      var value = svgEl('circle', {
        cx: 70, cy: 70, r: R, fill: 'none', 'stroke-width': 11, 'stroke-linecap': 'round',
        'stroke-dasharray': len + ' ' + C, 'stroke-dashoffset': offset,
        transform: 'rotate(-90 70 70)'
      });
      value.style.stroke = state.hasSmart ? scoreColor(dim.score) : 'var(--border)';
      svg.appendChild(value);

      var mid = (-90 + index * 72 + 36) * Math.PI / 180;
      var tick = svgEl('text', {
        x: 70 + 38 * Math.cos(mid), y: 70 + 38 * Math.sin(mid) + 3.5,
        'text-anchor': 'middle', 'class': 'ring-tick'
      });
      tick.textContent = dim.key;
      tick.style.fill = state.hasSmart ? scoreColor(dim.score) : 'var(--subtle)';
      svg.appendChild(tick);
    });

    var center = svgEl('text', { x: 70, y: 70, 'text-anchor': 'middle', 'class': 'ring-total' });
    center.textContent = state.hasSmart ? String(total) : '—';
    var caption = svgEl('text', { x: 70, y: 87, 'text-anchor': 'middle', 'class': 'ring-caption' });
    caption.textContent = state.hasSmart ? '总分 / 100' : '尚未质检';
    svg.appendChild(center);
    svg.appendChild(caption);
    box.appendChild(svg);
  }

  function renderDims(deltas) {
    deltas = deltas || {};
    var list = $('dim-list');
    if (!list) { return; }
    list.textContent = '';

    state.dims.forEach(function (dim) {
      var delta = Number(deltas[dim.key] || 0);
      var card = document.createElement('article');
      card.className = 'dim-card' + (dim.low ? ' is-low' : '') + (delta > 0 ? ' is-improved' : '');
      card.dataset.dim = dim.key;

      var head = document.createElement('div');
      head.className = 'dim-head';
      var badge = document.createElement('span');
      badge.className = 'dim-badge';
      badge.setAttribute('aria-hidden', 'true');
      badge.textContent = dim.key;
      var name = document.createElement('div');
      name.className = 'dim-name';
      var strong = document.createElement('strong');
      strong.textContent = dim.label;
      var small = document.createElement('small');
      small.textContent = dim.comment || '暂无评分依据';
      name.appendChild(strong);
      name.appendChild(small);
      var score = document.createElement('span');
      score.className = 'dim-score' + (dim.low ? ' is-low' : '');
      score.innerHTML = String(dim.score) + '<em> /20</em>';
      head.appendChild(badge);
      head.appendChild(name);
      if (delta !== 0) {
        var deltaBadge = document.createElement('em');
        deltaBadge.className = 'delta-badge' + (delta < 0 ? ' is-down' : '');
        deltaBadge.textContent = (delta > 0 ? '+' : '') + delta;
        head.appendChild(deltaBadge);
      }
      head.appendChild(score);
      card.appendChild(head);

      var bar = document.createElement('div');
      bar.className = 'dim-bar';
      var fill = document.createElement('i');
      fill.style.width = Math.min(100, dim.score / 20 * 100) + '%';
      bar.appendChild(fill);
      card.appendChild(bar);

      if (dim.suggestion) {
        var canCheck = DATA.can_apply;
        var wrap;
        if (canCheck) {
          wrap = document.createElement('label');
          wrap.className = 'dim-suggestion';
          var box = document.createElement('input');
          box.type = 'checkbox';
          box.dataset.dim = dim.key;
          box.addEventListener('change', updateAdoptButton);
          wrap.appendChild(box);
        } else {
          wrap = document.createElement('div');
          wrap.className = 'dim-suggestion';
        }
        var body = document.createElement('div');
        body.className = 'dim-suggestion-body';
        var tag = document.createElement('span');
        tag.className = 'suggestion-tag';
        tag.textContent = '改写建议 → ' + (dim.target_field || '目标文本');
        var text = document.createElement('p');
        text.textContent = dim.suggestion;
        body.appendChild(tag);
        body.appendChild(text);
        wrap.appendChild(body);
        card.appendChild(wrap);
      } else if (!dim.low && state.hasSmart) {
        var note = document.createElement('p');
        note.className = 'dim-note';
        note.textContent = '该维度已达标，无改写建议。';
        card.appendChild(note);
      }

      list.appendChild(card);
    });
  }

  function renderGate() {
    var total = totalScore();
    var pass = state.hasSmart && !anyLow() && total >= 60;

    var stateEl = $('gate-state');
    if (stateEl) {
      stateEl.textContent = pass ? '满足提交条件，可发起承接会签' : (state.hasSmart ? '存在短板维度，需整改后提交' : '尚未完成五维质检');
      stateEl.className = 'gate-state ' + (pass ? 'is-pass' : (state.hasSmart ? 'is-fail' : 'is-idle'));
    }
    var detailEl = $('gate-detail');
    if (detailEl) {
      detailEl.textContent = '总分 ' + total + ' / 100 · 维度门槛 ≥ ' + DATA.min_score + ' 分 · 提交门槛总分 ≥ 60 分';
    }
    var blockEl = $('gate-block');
    if (blockEl) {
      if (state.block) {
        blockEl.textContent = '当前阻断：' + state.block;
        blockEl.hidden = false;
      } else {
        blockEl.hidden = true;
      }
    }

    var ownerOk = !!DATA.objective.owner_id;
    setGate('gate-owner', ownerOk);
    setGate('gate-smart', state.hasSmart);
    setGate('gate-dims', state.hasSmart && !anyLow());
    setGate('gate-total', state.hasSmart && total >= 60);
    var totalEl = $('gate-total');
    if (totalEl) {
      totalEl.textContent = '总分 ≥ 60 分（当前 ' + total + '）';
    }

    var submitBtn = $('submit-button');
    if (submitBtn) {
      submitBtn.disabled = !!state.block;
      submitBtn.title = state.block || '满足提交条件';
    }
    var flowBlock = $('flow-block');
    if (flowBlock) {
      if (state.block) {
        flowBlock.textContent = '提交门禁未通过：' + state.block + '。可采纳低维建议后重试。';
        flowBlock.className = 'flow-block';
        flowBlock.hidden = false;
      } else if (submitBtn) {
        flowBlock.textContent = '提交门禁已通过，可发起承接会签。';
        flowBlock.className = 'flow-block is-ok';
        flowBlock.hidden = false;
      } else {
        flowBlock.hidden = true;
      }
    }
  }

  function setGate(id, ok) {
    var el = $(id);
    if (!el) { return; }
    el.className = ok ? 'ok' : 'miss';
  }

  function updateAdoptButton() {
    var button = $('adopt-button');
    if (!button) { return; }
    var count = document.querySelectorAll('#dim-list input[type="checkbox"]:checked').length;
    button.disabled = count === 0;
    button.textContent = count ? '采纳所选建议（' + count + '）' : '采纳所选建议';
  }

  function renderAll(deltas) {
    renderRing();
    renderDims(deltas);
    renderGate();
    updateAdoptButton();
  }

  /* ---------------- 交互 ---------------- */
  function withBusy(button, busyText, task) {
    if (button) { button.disabled = true; }
    var original = button ? button.textContent : '';
    if (button) { button.textContent = busyText; }
    return task().finally(function () {
      if (button) { button.textContent = original; button.disabled = false; }
      updateAdoptButton();
    });
  }

  function checkAgain() {
    var button = $('recheck-button');
    withBusy(button, '质检中…', function () {
      return post(DATA.urls.check, { objective_id: DATA.objective.objective_id }).then(function (body) {
        applySmart(body.data.smart, body.data.block_reason);
        applySmartMeta(body.data.smart_meta);
        renderAll({});
        toast('质检完成：总分 ' + totalScore() + ' / 100' + (state.block ? '，仍有门禁未过' : ''));
      }).catch(function (error) { toast(error.message, true); });
    });
  }

  function adoptSuggestions() {
    var checked = Array.prototype.map.call(
      document.querySelectorAll('#dim-list input[type="checkbox"]:checked'),
      function (box) { return box.dataset.dim; }
    );
    if (!checked.length) { return; }
    var button = $('adopt-button');
    withBusy(button, '改写中…', function () {
      return post(DATA.urls.adopt, { objective_id: DATA.objective.objective_id, dims: checked }).then(function (body) {
        var data = body.data;
        var deltas = {};
        DIM_ORDER.forEach(function (key) {
          deltas[key] = Number(data.after.dims[key] || 0) - Number(data.before.dims[key] || 0);
        });
        applySmart(data.smart, data.block_reason);
        applySmartMeta(data.smart_meta);
        renderAll(deltas);
        var changed = (data.applied || []).map(function (item) { return item.dim; }).join('、');
        toast('已采纳并改写（' + changed + '），总分 ' + data.before.total + ' → ' + data.after.total);
      }).catch(function (error) { toast(error.message, true); });
    });
  }

  function submitConfirm() {
    window.NorthModal.confirm({
      title: '提交确认',
      message: '确认将该目标提交给「' + DATA.objective.owner_name + '」进行承接会签？',
      impact: '提交后目标进入「待承接」，字段暂时冻结；责任人驳回后会退回草稿。',
      confirmText: '提交确认'
    }).then(function (ok) {
      if (!ok) { return; }
      post(DATA.urls.publish, { objective_id: DATA.objective.objective_id, action: 'submit' }).then(function (body) {
        toast(body.message);
        setTimeout(function () { window.location.reload(); }, 700);
      }).catch(function (error) { toast(error.message, true); });
    });
  }

  function countersignConfirm() {
    window.NorthModal.confirm({
      title: '确认承接',
      message: '确认承接「' + DATA.objective.title + '」并在后续执行中负责达成？',
      impact: '确认后目标进入「已承接」，等待管理员发布；发布后成功标准与时限将快照留存。',
      confirmText: '确认承接'
    }).then(function (ok) {
      if (!ok) { return; }
      post(DATA.urls.countersign, { objective_id: DATA.objective.objective_id, action: 'confirm' }).then(function (body) {
        toast(body.message);
        setTimeout(function () { window.location.reload(); }, 700);
      }).catch(function (error) { toast(error.message, true); });
    });
  }

  function countersignReject() {
    var wrap = document.createElement('label');
    wrap.className = 'modal-field';
    var label = document.createElement('span');
    label.textContent = '驳回原因（必填，≤200 字）';
    var area = document.createElement('textarea');
    area.maxLength = 200;
    area.placeholder = '说明无法承接的原因，将记入目标日志并退回草稿。';
    wrap.appendChild(label);
    wrap.appendChild(area);
    var error = document.createElement('p');
    error.className = 'modal-error';
    error.textContent = '请填写驳回原因。';
    error.hidden = true;
    var content = document.createElement('div');
    content.appendChild(wrap);
    content.appendChild(error);

    window.NorthModal.open({
      title: '驳回承接',
      content: content,
      confirmText: '确认驳回',
      danger: true,
      autoFocus: 'textarea',
      onConfirm: function () {
        var note = area.value.trim();
        if (!note) { error.hidden = false; area.focus(); return false; }
        post(DATA.urls.countersign, { objective_id: DATA.objective.objective_id, action: 'reject', note: note }).then(function (body) {
          toast(body.message);
          setTimeout(function () { window.location.reload(); }, 700);
        }).catch(function (err) { toast(err.message, true); });
        return true;
      }
    });
  }

  /* ---------------- 绑定 ---------------- */
  var recheck = $('recheck-button');
  if (recheck) { recheck.addEventListener('click', checkAgain); }
  var adopt = $('adopt-button');
  if (adopt) { adopt.addEventListener('click', adoptSuggestions); }
  var submit = $('submit-button');
  if (submit) { submit.addEventListener('click', submitConfirm); }
  var accept = $('accept-button');
  if (accept) { accept.addEventListener('click', countersignConfirm); }
  var reject = $('reject-button');
  if (reject) { reject.addEventListener('click', countersignReject); }

  applySmart(DATA.has_smart ? (function () {
    var smart = {};
    (DATA.dims || []).forEach(function (dim) {
      smart[dim.key] = { score: dim.score, comment: dim.comment, suggestion: dim.suggestion };
    });
    return smart;
  })() : null, DATA.block_reason);
  renderAll({});
})();
