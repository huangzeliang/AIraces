/* B2 预警与指导：提醒中心（状态筛选 / 分页 / 已读 / 确认）、AI 过程指导、连续异常补偿计算。 */
(function () {
  'use strict';

  var esc = window.Ns.escapeHtml;

  /* ---------------- 提醒中心 ---------------- */
  var rem = { status: '', page: 1, totalPages: 1 };
  var listEl = document.getElementById('wn-rem-list');
  var emptyEl = document.getElementById('wn-rem-empty');
  var pageEl = document.getElementById('wn-rem-page');
  var prevEl = document.getElementById('wn-rem-prev');
  var nextEl = document.getElementById('wn-rem-next');
  var unreadEl = document.getElementById('wn-rem-unread');
  var readAllEl = document.getElementById('wn-read-all');

  function reminderItem(r) {
    var unread = r.status_code === 'unread';
    var actions = '<a class="button button-small button-secondary" href="' + esc(r.deep_link) + '">去处理</a>';
    if (unread) {
      actions += '<button class="button button-small" type="button" data-reminder-id="' + esc(r.reminder_id) + '" data-action="read">已读</button>' +
        '<button class="button button-small" type="button" data-reminder-id="' + esc(r.reminder_id) + '" data-action="ack">确认不再提醒</button>';
    }
    return '<li class="' + (unread ? 'is-unread' : '') + '">' +
      '<span class="ns-pill ' + (r.level === 'red' ? 'is-red' : 'is-yellow') + '">' + esc(r.level_label) + '</span>' +
      '<div class="wn-alert-body"><strong>' + esc(r.title) + '</strong><p>' + esc(r.body) + '</p>' +
      '<p class="ns-mini">' + esc(r.alert_type_label || '巡检提醒') + ' · ' +
      esc(String(r.created_at || '').slice(0, 16).replace('T', ' ')) + ' · ' + esc(r.status) +
      (r.ack_at ? '（已确认，3 天内不再打扰）' : '') + '</p></div>' +
      '<div class="wn-reminder-actions">' + actions + '</div></li>';
  }

  function syncUnread(count) {
    if (unreadEl && typeof count === 'number') { unreadEl.textContent = String(count); }
    if (readAllEl && typeof count === 'number') { readAllEl.disabled = !count; }
    var badge = document.getElementById('reminder-badge');
    if (badge && typeof count === 'number') {
      if (count > 0) { badge.textContent = String(count); } else { badge.remove(); }
    }
  }

  function renderReminders(data) {
    rem.page = data.page || 1;
    rem.totalPages = data.total_pages || 1;
    var items = data.items || [];
    if (listEl) { listEl.innerHTML = items.map(reminderItem).join(''); }
    if (emptyEl) { emptyEl.hidden = items.length > 0; }
    if (pageEl) { pageEl.textContent = rem.page + ' / ' + rem.totalPages; }
    if (prevEl) { prevEl.disabled = rem.page <= 1; }
    if (nextEl) { nextEl.disabled = rem.page >= rem.totalPages; }
    syncUnread(data.unread_count);
  }

  function loadReminders(page) {
    var url = '/api/ns/reminder/list?page=' + (page || 1);
    if (rem.status) { url += '&status=' + rem.status; }
    window.Ns.api.get(url).then(renderReminders);
  }

  Array.prototype.slice.call(document.querySelectorAll('.wn-tab')).forEach(function (tab) {
    tab.addEventListener('click', function () {
      Array.prototype.slice.call(document.querySelectorAll('.wn-tab')).forEach(function (t) {
        t.classList.remove('is-active');
      });
      tab.classList.add('is-active');
      rem.status = tab.getAttribute('data-status') || '';
      loadReminders(1);
    });
  });
  if (prevEl) {
    prevEl.addEventListener('click', function () {
      if (rem.page > 1) { loadReminders(rem.page - 1); }
    });
  }
  if (nextEl) {
    nextEl.addEventListener('click', function () {
      if (rem.page < rem.totalPages) { loadReminders(rem.page + 1); }
    });
  }
  if (readAllEl) {
    readAllEl.addEventListener('click', function () {
      readAllEl.disabled = true;
      window.Ns.api.post('/api/ns/reminder/read', { action: 'all' }).then(function (data) {
        window.Ns.toast('已将 ' + data.affected + ' 条提醒标记为已读。', 'success');
        loadReminders(rem.page);
      }).catch(function () { readAllEl.disabled = false; });
    });
  }
  if (listEl) {
    listEl.addEventListener('click', function (event) {
      var btn = event.target.closest ? event.target.closest('[data-reminder-id]') : null;
      if (!btn) { return; }
      var action = btn.getAttribute('data-action');
      var id = btn.getAttribute('data-reminder-id');
      btn.disabled = true;
      window.Ns.api.post('/api/ns/reminder/read', { action: action, reminder_ids: [id] }).then(function (data) {
        if (action === 'ack') {
          window.Ns.toast('已知悉，同类提醒 3 天内不再推送' +
            (data.suppress_until ? '（至 ' + data.suppress_until + '）' : '') + '。', 'success');
        } else {
          window.Ns.toast('已标记为已读。', 'success');
        }
        loadReminders(rem.page);
      }).catch(function () { btn.disabled = false; });
    });
  }

  /* ---------------- AI 过程指导 ---------------- */
  var gTarget = document.getElementById('wn-guidance-target');
  var gScene = document.getElementById('wn-guidance-scene');
  var gGo = document.getElementById('wn-guidance-go');
  var gResult = document.getElementById('wn-guidance-result');
  var SCENE_LABEL = { auto: '自动判断', lagging: '进度滞后', deviation: '进度偏差', blocked: '任务阻塞', streak: '连续异常' };
  var guidanceTarget = '';

  function renderGuidance(data) {
    var ctx = data.context || {};
    var rows = data.guidance || [];
    var html = '<div class="wn-gd-meta">' +
      '<span class="ns-pill is-purple no-dot">' + esc(SCENE_LABEL[data.scene] || data.scene) + '</span>' +
      '<span class="ns-pill no-dot ' + (data.degraded ? 'is-yellow' : 'is-green') + '">' +
      (data.degraded ? '规则建议（大模型未配置或超时降级）' : '大模型建议') + '</span></div>' +
      '<p class="ns-mini wn-gd-title">' + esc(ctx.title || data.target_id) +
      ' · 完成 ' + esc(ctx.progress) + '% vs 时间 ' + esc(ctx.time_progress) + '%（差值 ' + esc(ctx.delta) + ' pp）</p>';
    if (!rows.length) {
      html += '<p class="ns-empty">暂无可用建议。</p>';
    } else {
      html += '<ol class="wn-gd-list">';
      rows.forEach(function (item) {
        html += '<li><strong>' + esc(item.action) + '</strong>' +
          (item.expect ? '<p>预期：' + esc(item.expect) + '</p>' : '') +
          (item.basis ? '<p class="ns-mini">依据：' + esc(item.basis) + '</p>' : '') +
          '<button class="button button-small button-primary" type="button" data-adopt-id="' + esc(item.adopt_id) + '"' +
          ' data-action="' + esc(item.action) + '" data-expect="' + esc(item.expect || '') +
          '" data-basis="' + esc(item.basis || '') + '">采纳</button></li>';
      });
      html += '</ol>';
    }
    gResult.innerHTML = html;
  }

  function generate(targetId, scene) {
    if (!targetId) {
      window.Ns.toast('请先选择指导对象。', 'error');
      if (gTarget) { gTarget.focus(); }
      return;
    }
    guidanceTarget = targetId;
    if (gGo) { gGo.disabled = true; }
    gResult.innerHTML = '<p class="ns-empty">正在生成建议…</p>';
    window.Ns.api.post('/api/ns/guidance/generate', {
      target_type: 'task', target_id: targetId, scene: scene || 'auto'
    }).then(function (data) {
      if (gGo) { gGo.disabled = false; }
      renderGuidance(data);
    }).catch(function () {
      if (gGo) { gGo.disabled = false; }
      gResult.innerHTML = '<p class="ns-empty">生成失败，请稍后重试。</p>';
    });
  }

  if (gGo) {
    gGo.addEventListener('click', function () {
      generate(gTarget ? gTarget.value : '', gScene ? gScene.value : 'auto');
    });
  }
  if (gResult) {
    gResult.addEventListener('click', function (event) {
      var btn = event.target.closest ? event.target.closest('[data-adopt-id]') : null;
      if (!btn) { return; }
      btn.disabled = true;
      window.Ns.api.post('/api/ns/guidance/generate', {
        adopt_id: btn.getAttribute('data-adopt-id'),
        target_type: 'task',
        target_id: guidanceTarget,
        action: btn.getAttribute('data-action'),
        expect: btn.getAttribute('data-expect'),
        basis: btn.getAttribute('data-basis')
      }).then(function (data) {
        window.Ns.toast(data.duplicated ? '该建议此前已采纳，无需重复。' : '已采纳并写入进度日志。', 'success');
        btn.textContent = '已采纳';
        btn.classList.remove('button-primary');
      }).catch(function () { btn.disabled = false; });
    });
  }
  Array.prototype.slice.call(document.querySelectorAll('[data-guidance-task]')).forEach(function (btn) {
    btn.addEventListener('click', function () {
      var taskId = btn.getAttribute('data-guidance-task');
      if (gTarget) {
        Array.prototype.slice.call(gTarget.options).forEach(function (opt) {
          if (opt.value === taskId) { gTarget.value = taskId; }
        });
      }
      generate(taskId, gScene ? gScene.value : 'auto');
      var side = document.querySelector('.wn-side');
      if (side && side.scrollIntoView) { side.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    });
  });

  /* ---------------- 连续异常补偿计算（dry-run） ---------------- */
  var chainBtn = document.getElementById('wn-chain-check');
  var chainResult = document.getElementById('wn-chain-result');
  if (chainBtn && chainResult) {
    chainBtn.addEventListener('click', function () {
      chainBtn.disabled = true;
      window.Ns.api.post('/api/ns/today/alert-chain', { dry_run: true }).then(function (data) {
        chainBtn.disabled = false;
        var rows = data.rows || [];
        var html = '<h3>补偿计算 · ' + esc(data.as_of_date) + '（dry-run，不写入）</h3>';
        if (!rows.length) {
          html += '<p class="ns-empty">当前没有连续异常命中，无需补偿投递。</p>';
        } else {
          html += '<ul class="wn-chain-rows">';
          rows.forEach(function (row) {
            html += '<li>' +
              '<span class="ns-pill ' + (row.level === 'red' ? 'is-red' : 'is-yellow') + '">' +
              (row.role === 'manager' ? '上级提醒' : '员工提醒') + '</span>' +
              '<div><strong>' + esc(row.title) + '</strong>' +
              '<p class="ns-mini">' + esc(row.subject_name) + ' · 连续 ' + esc(row.days) + ' 天 · ' +
              esc(row.window_start) + ' ~ ' + esc(row.window_end) + ' · 最低 ' + esc(row.min_progress) + '%</p></div></li>';
          });
          html += '</ul><p class="ns-mini">命中 ' + rows.length +
            ' 条：正式巡检将按 2.7.4 双角色文案入库；收件人确认过的同类提醒 3 天内不再推送。</p>';
        }
        chainResult.innerHTML = html;
        chainResult.hidden = false;
      }).catch(function () { chainBtn.disabled = false; });
    });
  }
})();
