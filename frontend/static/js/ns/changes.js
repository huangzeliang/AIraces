/* B3 变更管理：发起变更（原因结构化 + 新版本写档）与审批通过 / 驳回。 */
(function () {
  'use strict';

  var CFG = window.CH_CONFIG || {};
  var esc = window.Ns.escapeHtml;
  function byId(id) { return document.getElementById(id); }
  function slice10(value) { return String(value || '').slice(0, 10); }

  /* ---------------- 发起变更 ---------------- */
  var objSel = byId('ch-a-objective');
  var typeSel = byId('ch-a-type');
  var fields = byId('ch-a-fields');
  var freezeHint = byId('ch-a-freeze');
  var form = byId('ch-apply');

  function objectiveOf(id) {
    var hit = null;
    (CFG.objectives || []).forEach(function (o) { if (o.objective_id === id) { hit = o; } });
    return hit;
  }

  function freezeText(obj) {
    if (!obj || !obj.freeze) { return ''; }
    if (obj.freeze.frozen) { return obj.freeze.reason + (CFG.is_admin ? '（超管可勾选特批提交）' : ''); }
    if (obj.freeze.days_left !== null && obj.freeze.days_left !== undefined) {
      return '距周期结束 ' + obj.freeze.days_left + ' 天；最后 ' + obj.freeze.freeze_days + ' 天进入冻结窗口。';
    }
    return '';
  }

  function fieldHtml(obj, type) {
    if (!obj) { return '<p class="ns-empty">请先选择要变更的目标，再填写变更内容。</p>'; }
    if (type === 'content') {
      var krRows = (obj.krs || []).map(function (kr) {
        return '<div class="ch-kr-row"><span>' + esc(kr.kr_name || kr.kr_id) +
          (kr.unit ? '（' + esc(kr.unit) + '）' : '') + '</span>' +
          '<input class="ns-input" type="number" step="any" data-kr-id="' + esc(kr.kr_id) + '" value="' +
          esc(kr.target_value === null || kr.target_value === undefined ? '' : kr.target_value) + '" aria-label="' +
          esc(kr.kr_name || kr.kr_id) + '目标值"></div>';
      }).join('') || '<p class="ns-mini">该目标暂无关键结果，可仅调整目标内容与成功标准。</p>';
      return '<div class="ns-field"><label for="ch-a-title">目标内容<span class="req">*</span>（≤60 字）</label>' +
        '<input class="ns-input" id="ch-a-title" maxlength="60" value="' + esc(obj.title) + '"></div>' +
        '<div class="ns-field"><label for="ch-a-criteria">成功标准（每行一条，至少 1 条）<span class="req">*</span></label>' +
        '<textarea class="ns-textarea" id="ch-a-criteria" rows="3">' + esc((obj.criteria || []).join('\n')) + '</textarea></div>' +
        '<div class="ns-field"><span class="ns-label">关键结果目标值</span><div class="ch-kr-rows">' + krRows + '</div></div>';
    }
    if (type === 'due_date') {
      return '<div class="ns-grid cols-2">' +
        '<div class="ns-field"><label for="ch-a-start">开始日期<span class="req">*</span></label>' +
        '<input class="ns-input" type="date" id="ch-a-start" value="' + esc(slice10(obj.plan_start)) + '"></div>' +
        '<div class="ns-field"><label for="ch-a-end">结束日期<span class="req">*</span></label>' +
        '<input class="ns-input" type="date" id="ch-a-end" value="' + esc(slice10(obj.plan_end)) + '"></div></div>';
    }
    if (type === 'owner') {
      var options = (CFG.owners || []).map(function (u) {
        return '<option value="' + esc(u.user_id) + '"' + (u.user_id === obj.owner_id ? ' selected' : '') +
          '>' + esc(u.name) + '（' + esc(u.user_id) + '）</option>';
      }).join('');
      return '<div class="ns-field"><label for="ch-a-owner">新责任人<span class="req">*</span></label>' +
        '<select class="ns-select" id="ch-a-owner"><option value="">选择责任人…</option>' + options + '</select>' +
        '<span class="hint">当前责任人：' + esc(obj.owner_name || '—') + '</span></div>';
    }
    return '<div class="ns-field"><label for="ch-a-weight">目标权重<span class="req">*</span>（0~100）</label>' +
      '<input class="ns-input" type="number" id="ch-a-weight" min="0.1" max="100" step="0.1" value="' +
      esc(obj.weight === null || obj.weight === undefined ? '' : obj.weight) + '">' +
      '<span class="hint">权重将影响目标达成率汇总中的占比。</span></div>';
  }

  function paint() {
    if (!objSel || !typeSel || !fields) { return; }
    var obj = objectiveOf(objSel.value);
    if (freezeHint) {
      freezeHint.textContent = freezeText(obj);
      freezeHint.classList.toggle('is-warn', !!(obj && obj.freeze && obj.freeze.frozen));
    }
    fields.innerHTML = fieldHtml(obj, typeSel.value);
  }

  function buildSnapshot(obj, type) {
    if (type === 'content') {
      var title = (byId('ch-a-title').value || '').trim();
      if (!title) { window.Ns.toast('目标内容不能为空。', 'error'); return null; }
      var criteria = (byId('ch-a-criteria').value || '').split('\n')
        .map(function (row) { return row.trim(); })
        .filter(function (row) { return !!row; });
      if (!criteria.length) { window.Ns.toast('成功标准至少保留 1 条。', 'error'); return null; }
      var krs = [];
      Array.prototype.slice.call(fields.querySelectorAll('[data-kr-id]')).forEach(function (input) {
        var value = input.value === '' ? null : Number(input.value);
        if (value !== null && isNaN(value)) { value = null; }
        krs.push({ kr_id: input.getAttribute('data-kr-id'), target_value: value });
      });
      var snapshot = { title: title, success_criteria: criteria };
      if (krs.length) { snapshot.krs = krs; }
      return snapshot;
    }
    if (type === 'due_date') {
      var start = byId('ch-a-start').value;
      var end = byId('ch-a-end').value;
      if (!start || !end) { window.Ns.toast('请选择开始与结束日期。', 'error'); return null; }
      return { plan_start: start, plan_end: end };
    }
    if (type === 'owner') {
      var owner = byId('ch-a-owner').value;
      if (!owner) { window.Ns.toast('请选择新责任人。', 'error'); return null; }
      return { owner_id: owner };
    }
    var weight = Number(byId('ch-a-weight').value);
    if (!weight || weight <= 0 || weight > 100) { window.Ns.toast('权重需在 0~100 之间。', 'error'); return null; }
    return { weight: weight };
  }

  if (objSel && typeSel && fields) {
    objSel.addEventListener('change', paint);
    typeSel.addEventListener('change', paint);
    paint();
  }

  if (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var obj = objectiveOf(objSel.value);
      if (!obj) { window.Ns.toast('请先选择目标。', 'error'); objSel.focus(); return; }
      var type = typeSel.value;
      var snapshot = buildSnapshot(obj, type);
      if (!snapshot) { return; }
      var reason = byId('ch-a-reason').value;
      if (!reason) { window.Ns.toast('请选择原因分类。', 'error'); byId('ch-a-reason').focus(); return; }
      var reasonText = (byId('ch-a-text').value || '').trim();
      if (!reasonText) { window.Ns.toast('原因说明必填。', 'error'); byId('ch-a-text').focus(); return; }
      var impact = (byId('ch-a-impact').value || '').trim();
      if (!impact) { window.Ns.toast('影响说明必填（是否影响关键路径 / 上下游）。', 'error'); byId('ch-a-impact').focus(); return; }

      var body = {
        objective_id: obj.objective_id,
        change_type: type,
        version: obj.version,
        reason_code: reason,
        reason_text: reasonText,
        impact_note: impact,
        after_snapshot: snapshot
      };
      var forceEl = byId('ch-a-force');
      if (forceEl && forceEl.checked) { body.force = true; }

      var submitBtn = byId('ch-a-submit');
      if (submitBtn) { submitBtn.disabled = true; }
      window.Ns.api.post('/api/ns/change/apply', body).then(function (data) {
        var message = '已提交 ' + data.change_id + '（' + data.status_label + '），待 ' +
          (data.approver_name || '上级') + ' 审批。';
        if (data.predicted_delay_days) { message += ' 预计延期 ' + data.predicted_delay_days + ' 天。'; }
        window.Ns.toast(message, 'success');
        setTimeout(function () { window.location.reload(); }, 900);
      }).catch(function () {
        if (submitBtn) { submitBtn.disabled = false; }
      });
    });
  }

  /* ---------------- 审批通过 / 驳回 ---------------- */
  Array.prototype.slice.call(document.querySelectorAll('.ch-approve')).forEach(function (box) {
    var changeId = box.getAttribute('data-change-id');
    var noteEl = box.querySelector('textarea');

    Array.prototype.slice.call(box.querySelectorAll('button[data-action]')).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var action = btn.getAttribute('data-action');
        var note = (noteEl.value || '').trim();
        if (action === 'reject' && !note) {
          window.Ns.toast('驳回必须填写审批意见。', 'error');
          noteEl.focus();
          return;
        }
        var danger = action === 'reject';
        window.NorthModal.confirm({
          title: danger ? '驳回变更申请' : '通过变更申请',
          message: danger ? '驳回后申请人需修改后重新提交。' : '通过后按新版本执行，原基线保留并可追溯。',
          impact: note ? '审批意见：' + note : '',
          danger: danger,
          confirmText: danger ? '确认驳回' : '通过并写入新版本'
        }).then(function (ok) {
          if (!ok) { return; }
          btn.disabled = true;
          window.Ns.api.post('/api/ns/change/approve', {
            change_id: changeId, action: action, note: note
          }).then(function () {
            window.Ns.toast(action === 'approve' ? '已通过，目标已写入新版本。' : '已驳回。', 'success');
            setTimeout(function () { window.location.reload(); }, 700);
          }).catch(function () { btn.disabled = false; });
        });
      });
    });
  });
})();
