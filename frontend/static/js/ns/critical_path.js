/* B4 关键路径与阻塞：依赖网络图、影响传导模拟、依赖维护（含环检测提示）。 */
(function () {
  'use strict';

  var CFG = window.CP_CONFIG || {};
  var esc = window.Ns.escapeHtml;
  var graph = CFG.graph || { nodes: [], edges: [] };

  /* ---------------- 依赖网络图 ---------------- */
  var mount = document.getElementById('cp-network');
  if (mount && window.NsCharts) {
    mount.innerHTML = window.NsCharts.network(graph);
    function openTask(node) {
      var id = node.getAttribute('data-task-id');
      if (id) { window.location.href = '/ns/progress?task_id=' + encodeURIComponent(id) + '&from=critical-path'; }
    }
    mount.addEventListener('click', function (event) {
      var node = event.target.closest ? event.target.closest('[data-task-id]') : null;
      if (node) { openTask(node); }
    });
    mount.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') { return; }
      var node = event.target.closest ? event.target.closest('[data-task-id]') : null;
      if (node) { event.preventDefault(); openTask(node); }
    });
  }

  /* ---------------- 影响传导模拟 ---------------- */
  var impactTask = document.getElementById('cp-impact-task');
  var impactDays = document.getElementById('cp-impact-days');
  var impactGo = document.getElementById('cp-impact-go');
  var impactResult = document.getElementById('cp-impact-result');

  function renderImpact(data) {
    var html = '<div class="cp-impact-head">' +
      '<span class="ns-pill ' + (data.is_critical ? 'is-purple' : 'is-muted') + '">' +
      (data.is_critical ? '关键路径任务' : '非关键路径任务') + '</span>' +
      '<strong>' + esc(data.conclusion) + '</strong></div>' +
      '<p class="ns-mini">总时差 ' + esc(data.total_float_days) + ' 天 · 项目整体延期 ' +
      esc(data.project_delay_days) + ' 天</p>';
    var affected = data.affected_tasks || [];
    if (affected.length) {
      html += '<div class="ns-table-wrap"><table class="ns-table cp-impact-table">' +
        '<thead><tr><th>受影响任务</th><th>顺延</th><th>负责人</th><th>状态</th></tr></thead><tbody>';
      affected.forEach(function (task) {
        html += '<tr><td><a href="/ns/progress?task_id=' + esc(task.task_id) + '">' + esc(task.title) + '</a></td>' +
          '<td class="num">+' + esc(task.delay_days) + ' 天</td>' +
          '<td>' + esc((task.assignee_names || []).join('、') || '—') + '</td>' +
          '<td>' + esc(task.status_label || '') + '</td></tr>';
      });
      html += '</tbody></table></div>';
    }
    var suggestions = data.suggestions || [];
    if (suggestions.length) {
      html += '<ul class="cp-impact-sug">';
      suggestions.forEach(function (row) { html += '<li>' + esc(row) + '</li>'; });
      html += '</ul>';
    }
    impactResult.innerHTML = html;
  }

  if (impactGo) {
    impactGo.addEventListener('click', function () {
      if (!impactTask || !impactTask.value) {
        window.Ns.toast('请先选择任务。', 'error');
        return;
      }
      var delay = Number(impactDays && impactDays.value !== '' ? impactDays.value : 1);
      if (isNaN(delay) || delay < 0) {
        window.Ns.toast('假设延期天数需为 ≥0 的整数。', 'error');
        return;
      }
      impactGo.disabled = true;
      window.Ns.api.get('/api/ns/dep/impact?task_id=' + encodeURIComponent(impactTask.value) +
        '&delay_days=' + Math.round(delay)).then(function (data) {
        impactGo.disabled = false;
        renderImpact(data);
      }).catch(function () { impactGo.disabled = false; });
    });
  }

  /* ---------------- 依赖维护 ---------------- */
  var depForm = document.getElementById('cp-dep-form');
  var depResult = document.getElementById('cp-dep-result');
  var inferBtn = document.getElementById('cp-infer');

  function showDepResult(html, isError) {
    if (!depResult) { return; }
    depResult.innerHTML = html;
    depResult.classList.toggle('is-error', !!isError);
  }

  if (depForm) {
    depForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var from = document.getElementById('cp-dep-from').value;
      var to = document.getElementById('cp-dep-to').value;
      var type = document.getElementById('cp-dep-type').value;
      var lag = Number(document.getElementById('cp-dep-lag').value || 0);
      if (!from || !to) { window.Ns.toast('请选择前置与后置任务。', 'error'); return; }
      if (from === to) { window.Ns.toast('不允许任务依赖自身。', 'error'); return; }
      if (isNaN(lag)) { window.Ns.toast('间隔需为整数（可为负）。', 'error'); return; }
      var saveBtn = document.getElementById('cp-dep-save');
      if (saveBtn) { saveBtn.disabled = true; }
      window.Ns.api.post('/api/ns/dep/save', {
        mode: 'save',
        objective_id: CFG.objective_id || '',
        edges: [{ from_task_id: from, to_task_id: to, dep_type: type, lag_days: Math.round(lag) }]
      }).then(function (data) {
        window.Ns.toast('已保存 ' + data.saved_count + ' 条依赖' +
          (data.critical_path_changed ? '，关键路径已更新。' : '。'), 'success');
        setTimeout(function () { window.location.reload(); }, 700);
      }).catch(function (err) {
        if (saveBtn) { saveBtn.disabled = false; }
        var cycle = err && err.data && err.data.cycle_path;
        if (err && err.code === 1005 && cycle && cycle.length) {
          showDepResult('<div class="cp-cycle"><strong>依赖成环，保存被拒绝（错误码 1005）</strong>' +
            '<p>' + cycle.concat([cycle[0]]).map(esc).join(' → ') + '</p>' +
            '<p class="ns-mini">请调整依赖方向或删除冲突边后重试。</p></div>', true);
        } else {
          showDepResult('<p class="ns-mini">保存失败：' + esc((err && err.message) || '未知错误') + '</p>', true);
        }
      });
    });
  }

  if (inferBtn) {
    inferBtn.addEventListener('click', function () {
      if (!CFG.objective_id) {
        window.Ns.toast('请先在上方按单个目标筛选，再执行批量推断。', 'error');
        return;
      }
      window.NorthModal.confirm({
        title: '批量推断依赖',
        message: '按同层级任务的计划开始时间排序，自动生成 FS 依赖链（父任务 → 子任务）。已存在的同向依赖会被更新。',
        confirmText: '开始推断'
      }).then(function (ok) {
        if (!ok) { return; }
        inferBtn.disabled = true;
        window.Ns.api.post('/api/ns/dep/save', { mode: 'infer', objective_id: CFG.objective_id }).then(function (data) {
          window.Ns.toast('已推断并保存 ' + data.saved_count + ' 条依赖' +
            (data.critical_path_changed ? '，关键路径已更新。' : '。'), 'success');
          setTimeout(function () { window.location.reload(); }, 800);
        }).catch(function () { inferBtn.disabled = false; });
      });
    });
  }

  Array.prototype.slice.call(document.querySelectorAll('[data-dep-del]')).forEach(function (btn) {
    btn.addEventListener('click', function () {
      window.NorthModal.confirm({
        title: '删除依赖',
        message: '删除后关键路径将重新推演，影响范围可能变化。',
        impact: btn.getAttribute('data-from-name') + ' → ' + btn.getAttribute('data-to-name'),
        danger: true,
        confirmText: '确认删除'
      }).then(function (ok) {
        if (!ok) { return; }
        btn.disabled = true;
        window.Ns.api.post('/api/ns/dep/save', {
          mode: 'delete',
          objective_id: CFG.objective_id || '',
          edges: [{ dep_id: btn.getAttribute('data-dep-id') }]
        }).then(function () {
          window.Ns.toast('依赖已删除，关键路径已重算。', 'success');
          setTimeout(function () { window.location.reload(); }, 600);
        }).catch(function () { btn.disabled = false; });
      });
    });
  });
})();
