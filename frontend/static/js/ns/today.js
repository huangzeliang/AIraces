/* B6 日计划与任务：日期导航、三视图切换、日计划审核。 */
(function () {
  'use strict';

  var dateInput = document.getElementById('td-date');
  if (dateInput && dateInput.value) {
    var base = new Date(dateInput.value + 'T00:00:00');
    function shift(days) {
      var next = new Date(base.getTime() + days * 86400000);
      return next.toISOString().slice(0, 10);
    }
    var prev = document.getElementById('td-prev');
    var next = document.getElementById('td-next');
    if (prev) {
      prev.href = prev.href.replace('date=' + dateInput.value.slice(0, 10), 'date=' + shift(-1));
    }
    if (next) {
      next.href = next.href.replace('date=' + dateInput.value.slice(0, 10), 'date=' + shift(1));
    }
  }

  var tabs = Array.prototype.slice.call(document.querySelectorAll('.td-tab'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('.td-view'));
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var key = tab.getAttribute('data-view');
      tabs.forEach(function (item) {
        var active = item === tab;
        item.classList.toggle('is-active', active);
        item.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      panels.forEach(function (panel) {
        panel.hidden = panel.getAttribute('data-view') !== key;
      });
    });
  });

  var approveBtn = document.getElementById('td-approve');
  if (approveBtn) {
    approveBtn.addEventListener('click', function () {
      approveBtn.disabled = true;
      window.Ns.api.post('/api/ns/today/plan-approve', {
        plan_id: approveBtn.getAttribute('data-plan-id'),
        action: 'approve'
      }).then(function (data) {
        window.Ns.toast('已通过日计划（当日完成 ' + (data.day_progress === null || data.day_progress === undefined ? '—' : data.day_progress + '%') + '）', 'success');
        setTimeout(function () { window.location.reload(); }, 500);
      }).catch(function () { approveBtn.disabled = false; });
    });
  }

  var rejectSubmit = document.getElementById('td-reject-submit');
  if (rejectSubmit) {
    rejectSubmit.addEventListener('click', function () {
      var reason = (document.getElementById('td-reject-reason').value || '').trim();
      if (!reason) {
        window.Ns.toast('请填写驳回理由。', 'error');
        return;
      }
      rejectSubmit.disabled = true;
      window.Ns.api.post('/api/ns/today/plan-approve', {
        plan_id: rejectSubmit.getAttribute('data-plan-id'),
        action: 'reject',
        reason: reason
      }).then(function () {
        window.Ns.toast('已驳回日计划。', 'success');
        setTimeout(function () { window.location.reload(); }, 500);
      }).catch(function () { rejectSubmit.disabled = false; });
    });
  }
})();
