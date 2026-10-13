/* 数据管理（C2）页面脚本：文件选择/拖拽、导入校验预览、确认导入、脱敏切换与异常补录。
   零依赖实现，导入全流程走 fetch + FormData，后端负责校验与事务性落库。 */

(function () {
  'use strict';

  var dropzone = document.querySelector('#dropzone');
  var fileInput = document.querySelector('#import-file');
  var fileName = document.querySelector('#file-name');
  var previewBtn = document.querySelector('#preview-btn');
  var confirmBtn = document.querySelector('#confirm-btn');
  var statusNode = document.querySelector('#import-status');
  var resultBox = document.querySelector('#preview-result');
  var maskToggle = document.querySelector('#mask-toggle');

  var selectedFile = null;
  var previewPassed = false;

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function setStatus(message, tone) {
    if (!statusNode) return;
    statusNode.textContent = message || '';
    statusNode.className = 'import-status' + (tone ? ' is-' + tone : '');
  }

  /* ---------------- 文件选择与拖拽 ---------------- */
  function acceptFile(file) {
    if (!file) return;
    if (!/\.csv$/i.test(file.name)) {
      setStatus('仅支持 CSV 文件，请另存为 .csv 后重试。', 'error');
      return;
    }
    selectedFile = file;
    previewPassed = false;
    if (fileName) {
      fileName.hidden = false;
      fileName.textContent = '已选择：' + file.name + '（' + Math.max(1, Math.round(file.size / 1024)) + ' KB）';
    }
    if (previewBtn) previewBtn.disabled = false;
    if (confirmBtn) confirmBtn.disabled = true;
    if (resultBox) resultBox.hidden = true;
    setStatus('文件已就绪，先点「校验预览」检查数据。', '');
  }

  if (fileInput) {
    fileInput.addEventListener('change', function () {
      acceptFile(fileInput.files && fileInput.files[0]);
    });
  }

  if (dropzone) {
    ['dragenter', 'dragover'].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        dropzone.classList.add('is-dragover');
      });
    });
    ['dragleave', 'drop'].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        dropzone.classList.remove('is-dragover');
      });
    });
    dropzone.addEventListener('drop', function (event) {
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) acceptFile(files[0]);
    });
  }

  /* ---------------- 预览渲染 ---------------- */
  function renderPreview(payload) {
    if (!resultBox) return;
    if (!payload.ok) {
      resultBox.hidden = false;
      resultBox.innerHTML = '<p class="preview-invalid">' + escapeHtml(payload.message || '解析失败。') + '</p>';
      return;
    }

    var mapped = (payload.mapped || []).map(function (item) {
      return '<tr><td>' + escapeHtml(item.source) + '</td><td>' + escapeHtml(item.target) + '</td></tr>';
    }).join('');

    var invalid = '';
    if (payload.invalid_count) {
      invalid = '<p class="preview-invalid">发现 ' + payload.invalid_count + ' 行异常，确认导入后会进入异常数据区：</p><ul class="preview-invalid">' +
        (payload.invalid || []).slice(0, 8).map(function (item) {
          return '<li>第 ' + escapeHtml(item.line) + ' 行 — ' + escapeHtml(item.reason) + '</li>';
        }).join('') + '</ul>';
    }

    resultBox.hidden = false;
    resultBox.innerHTML =
      '<div class="preview-summary">' +
        '<span>文件 <b>' + escapeHtml(payload.filename) + '</b></span>' +
        '<span>编码 <b>' + escapeHtml(payload.encoding) + '</b></span>' +
        '<span>总行数 <b>' + payload.total + '</b></span>' +
        '<span>合法行 <b>' + payload.valid_count + '</b></span>' +
        '<span>异常行 <b>' + payload.invalid_count + '</b></span>' +
      '</div>' +
      '<table class="preview-table"><thead><tr><th>源表头</th><th>目标字段</th></tr></thead><tbody>' + mapped + '</tbody></table>' +
      invalid;
  }

  /* ---------------- 提交 ---------------- */
  function submit(dryRun) {
    if (!selectedFile) {
      setStatus('请先选择要导入的 CSV 文件。', 'error');
      return Promise.resolve();
    }
    var body = new FormData();
    body.append('import_file', selectedFile);
    body.append('dry_run', dryRun ? 'true' : 'false');

    setStatus(dryRun ? '正在校验…' : '正在导入…', '');
    if (previewBtn) previewBtn.disabled = true;
    if (confirmBtn) confirmBtn.disabled = true;

    return fetch('/data/import', { method: 'POST', body: body })
      .then(function (response) { return response.json().then(function (data) { return { ok: response.ok, data: data }; }); })
      .then(function (result) {
        var payload = result.data || {};
        if (!result.ok || !payload.ok) {
          renderPreview(payload);
          setStatus(payload.message || '处理失败，请检查文件后重试。', 'error');
          if (previewBtn) previewBtn.disabled = false;
          return;
        }
        if (dryRun) {
          renderPreview(payload);
          previewPassed = true;
          if (confirmBtn) confirmBtn.disabled = payload.valid_count === 0;
          if (previewBtn) previewBtn.disabled = false;
          setStatus(payload.valid_count
            ? '校验完成：合法 ' + payload.valid_count + ' 行，异常 ' + payload.invalid_count + ' 行。可以确认导入。'
            : '没有可导入的合法行，请修正文件后重试。', payload.valid_count ? 'ok' : 'error');
        } else {
          setStatus('导入完成：入库 ' + payload.imported + ' 行，异常 ' + payload.exceptions_added + ' 行。正在刷新…', 'ok');
          window.setTimeout(function () { window.location.reload(); }, 900);
        }
      })
      .catch(function () {
        setStatus('网络异常，导入未执行。请检查服务是否在运行后重试。', 'error');
        if (previewBtn) previewBtn.disabled = false;
      });
  }

  if (previewBtn) previewBtn.addEventListener('click', function () { submit(true); });
  if (confirmBtn) {
    confirmBtn.addEventListener('click', function () {
      if (!previewPassed) {
        setStatus('请先完成「校验预览」。', 'error');
        return;
      }
      if (window.confirm('确认把合法行写入任务库吗？异常行会进入异常数据区，不会覆盖既有数据。')) {
        submit(false);
      }
    });
  }

  /* ---------------- 脱敏开关 ---------------- */
  if (maskToggle) {
    maskToggle.addEventListener('change', function () {
      var url = new URL(window.location.href);
      if (maskToggle.checked) url.searchParams.set('mask', '1');
      else url.searchParams.delete('mask');
      window.location.href = url.toString();
    });
  }

  /* ---------------- 异常补录弹窗 ---------------- */
  var fixDialog = document.querySelector('#fix-dialog');
  var fixForm = document.querySelector('#fix-form');

  function toLocalInput(value) {
    if (!value) return '';
    var cleaned = String(value).replace(/\//g, '-');
    var match = cleaned.match(/^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/);
    if (!match) return '';
    return match[1] + 'T' + (match[2] || '18:00');
  }

  if (fixDialog && fixForm) {
    document.querySelectorAll('.fix-btn').forEach(function (button) {
      button.addEventListener('click', function () {
        fixForm.action = '/data/exception/' + encodeURIComponent(button.dataset.id) + '/resolve';
        fixForm.elements.title.value = button.dataset.title || '';
        fixForm.elements.priority.value = button.dataset.priority || '中';
        fixForm.elements.estimate_minutes.value = button.dataset.estimate || '';
        fixForm.elements.due_at.value = toLocalInput(button.dataset.due);
        var reason = document.querySelector('#fix-reason');
        if (reason) reason.textContent = button.dataset.reason || '';
        // 标记为「补录 / 修正」动作，后端据此走入库分支
        var actionField = fixForm.querySelector('input[name="action"]');
        if (!actionField) {
          actionField = document.createElement('input');
          actionField.type = 'hidden';
          actionField.name = 'action';
          fixForm.appendChild(actionField);
        }
        actionField.value = 'fix';
        fixDialog.showModal();
        fixForm.elements.title.focus();
      });
    });

    var closeBtn = document.querySelector('#fix-close');
    var cancelBtn = document.querySelector('#fix-cancel');
    if (closeBtn) closeBtn.addEventListener('click', function () { fixDialog.close(); });
    if (cancelBtn) cancelBtn.addEventListener('click', function () { fixDialog.close(); });
    fixDialog.addEventListener('click', function (event) {
      if (event.target === fixDialog) fixDialog.close();
    });
  }
})();
