/* B5 个人工作台交互：进度环增强、提醒已读、提醒中心快捷操作。 */
(function () {
  'use strict';

  function enhanceRings() {
    document.querySelectorAll('.js-ring').forEach(function (host) {
      var value = parseFloat(host.getAttribute('data-value'));
      var level = host.classList.contains('is-lagging') ? 'red' : 'green';
      host.innerHTML = NsCharts.ring({
        value: isNaN(value) ? null : value,
        size: 78, stroke: 8,
        level: level,
        caption: host.getAttribute('data-caption') || '',
        aria: '完成进度 ' + (isNaN(value) ? '—' : value + '%')
      });
      host.classList.add('is-ready');
    });
  }

  function bindReadAll() {
    var button = document.getElementById('wb-read-all');
    if (!button) { return; }
    button.addEventListener('click', function () {
      button.disabled = true;
      Ns.api.post('/api/ns/reminder/read', { action: 'all' }).then(function (data) {
        Ns.toast('已将 ' + data.affected + ' 条提醒标记为已读', 'success');
        document.querySelectorAll('.wb-rem.is-unread').forEach(function (row) {
          row.classList.remove('is-unread');
        });
        var badge = document.getElementById('reminder-badge');
        if (badge) { badge.remove(); }
      }).catch(function () {
        button.disabled = false;
      });
    });
  }

  function bindReminderOpen() {
    document.querySelectorAll('.wb-rem').forEach(function (row) {
      var link = row.querySelector('.wb-rem-title');
      if (!link) { return; }
      link.addEventListener('click', function () {
        if (!row.classList.contains('is-unread')) { return; }
        Ns.api.post('/api/ns/reminder/read', {
          action: 'read', reminder_ids: [row.getAttribute('data-reminder-id')]
        }, { silent: true }).catch(function () {});
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    enhanceRings();
    bindReadAll();
    bindReminderOpen();
  });
})();
