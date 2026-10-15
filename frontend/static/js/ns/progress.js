/* B1 进度更新：任务进度提交（含证据附件与回退原因校验）。 */
(function () {
  'use strict';

  var form = document.getElementById('pg-form');
  if (!form) { return; }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var progressInput = document.getElementById('pg-progress');
    var value = Number(progressInput.value);
    if (isNaN(value) || value < 0 || value > 100) {
      window.Ns.toast('进度必须是 0~100 的数字。', 'error');
      return;
    }
    var current = Number(form.getAttribute('data-current'));
    var backReason = (document.getElementById('pg-back').value || '').trim();
    if (value < current && !backReason) {
      window.Ns.toast('进度不得回退：请填写回退原因。', 'error');
      document.getElementById('pg-back').focus();
      return;
    }

    var payload = {
      task_id: form.getAttribute('data-task-id'),
      progress: value,
      note: (document.getElementById('pg-note').value || '').trim(),
      back_reason: backReason,
      milestone: document.getElementById('pg-milestone').checked,
      version: Number(form.getAttribute('data-version'))
    };
    var files = document.getElementById('pg-evidence').files;
    var body;
    if (files && files.length) {
      body = new FormData();
      body.append('payload', JSON.stringify(payload));
      for (var i = 0; i < files.length; i++) { body.append('evidence', files[i]); }
    } else {
      body = payload;
    }

    var submit = document.getElementById('pg-submit');
    submit.disabled = true;
    window.Ns.api.postForm('/api/ns/task/progress', body).then(function (data) {
      window.Ns.toast('进度已更新为 ' + data.progress + '%（' + data.health_label + '）', 'success');
      setTimeout(function () { window.location.reload(); }, 600);
    }).catch(function () {
      submit.disabled = false;
    });
  });
})();
