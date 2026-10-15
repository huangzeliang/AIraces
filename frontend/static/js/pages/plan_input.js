/* 计划录入（A1）页面脚本：字数计数 / 填充示例 / 清空二次确认 / 解析 loading / 一句话办事。
   零依赖实现；一句话办事结果全部通过 DOM API 渲染，避免拼接 HTML。 */

(function () {
  'use strict';

  var LIMIT = 2000;
  var DEFAULT_ERROR = '原始文本不能为空，请先输入一段计划描述。';

  var textarea = document.querySelector('#raw-text');
  var counter = document.querySelector('#char-count');
  var parseButton = document.querySelector('#parse-button');
  var parseForm = document.querySelector('#parse-form');
  var rawError = document.querySelector('#raw-error');
  var clearButton = document.querySelector('#clear-button');
  var clearDialog = document.querySelector('#clear-dialog');
  var clearCancel = document.querySelector('#clear-cancel');
  var clearConfirm = document.querySelector('#clear-confirm');

  /* ---------------- 字数计数与提交状态 ---------------- */
  function setError(message) {
    if (message) {
      rawError.textContent = message;
      rawError.hidden = false;
      textarea.classList.add('is-invalid');
    } else {
      rawError.textContent = DEFAULT_ERROR;
      rawError.hidden = true;
      textarea.classList.remove('is-invalid');
    }
  }

  function updateCounter() {
    var length = textarea.value.length;
    counter.textContent = length + '/' + LIMIT;
    counter.classList.toggle('is-warn', length >= LIMIT);
    counter.classList.toggle('is-over', length > LIMIT);
    parseButton.disabled = length > LIMIT;
  }

  textarea.addEventListener('input', function () {
    updateCounter();
    if (textarea.value.trim() && textarea.value.length <= LIMIT) {
      setError(null);
    } else if (textarea.value.length > LIMIT) {
      setError('已达到 2000 字上限，请精简后再解析。');
    }
  });

  /* ---------------- 填充示例 / 清空 ---------------- */
  document.querySelectorAll('.example-chip').forEach(function (button) {
    button.addEventListener('click', function () {
      textarea.value = button.getAttribute('data-example') || '';
      updateCounter();
      setError(null);
      textarea.focus();
    });
  });

  clearButton.addEventListener('click', function () {
    if (!textarea.value) {
      textarea.focus();
      return;
    }
    clearDialog.showModal();
  });
  clearCancel.addEventListener('click', function () { clearDialog.close(); });
  clearConfirm.addEventListener('click', function () {
    textarea.value = '';
    updateCounter();
    setError(null);
    clearDialog.close();
    textarea.focus();
  });

  /* ---------------- 解析提交 ---------------- */
  parseForm.addEventListener('submit', function (event) {
    if (!textarea.value.trim()) {
      event.preventDefault();
      setError(DEFAULT_ERROR);
      textarea.focus();
      return;
    }
    if (textarea.value.length > LIMIT) {
      event.preventDefault();
      setError('已达到 2000 字上限，请精简后再解析。');
      textarea.focus();
      return;
    }
    parseButton.disabled = true;
    parseButton.textContent = '解析中…';
  });

  window.addEventListener('pageshow', function () {
    parseButton.disabled = false;
    parseButton.textContent = '解析计划';
    updateCounter();
  });

  /* ---------------- 一句话办事（A4） ---------------- */
  var commandForm = document.querySelector('#command-form');
  var commandInput = document.querySelector('#command-input');
  var commandButton = document.querySelector('#command-button');
  var commandResult = document.querySelector('#command-result');

  function renderCommand(result) {
    commandResult.innerHTML = '';

    var title = document.createElement('h3');
    title.textContent = result.title || '执行结果';
    commandResult.appendChild(title);

    var lines = result.lines || [];
    if (lines.length) {
      var list = document.createElement('ul');
      list.className = 'command-lines';
      lines.forEach(function (line) {
        var item = document.createElement('li');
        item.textContent = line;
        list.appendChild(item);
      });
      commandResult.appendChild(list);
    }

    if (result.note) {
      var note = document.createElement('p');
      note.className = 'command-note';
      note.textContent = result.note;
      commandResult.appendChild(note);
    }

    if (result.link && result.link.path) {
      var link = document.createElement('a');
      link.className = 'button button-secondary command-link';
      link.href = result.link.path;
      link.textContent = result.link.label || '前往';
      commandResult.appendChild(link);
    }

    if (result.options && result.options.length) {
      var options = document.createElement('div');
      options.className = 'command-options';
      result.options.forEach(function (text) {
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'command-option';
        chip.textContent = text;
        chip.addEventListener('click', function () {
          commandInput.value = text;
          runCommand();
        });
        options.appendChild(chip);
      });
      commandResult.appendChild(options);
    }

    commandResult.hidden = false;
  }

  function runCommand() {
    var query = commandInput.value.trim();
    if (!query) {
      commandInput.focus();
      return;
    }
    commandButton.disabled = true;
    commandButton.textContent = '执行中…';
    fetch('/plan/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: query })
    }).then(function (response) {
      return response.json();
    }).then(function (result) {
      renderCommand(result);
    }).catch(function () {
      renderCommand({ title: '指令执行失败', lines: ['网络或服务异常，请稍后重试。'] });
    }).then(function () {
      commandButton.disabled = false;
      commandButton.textContent = '执行';
    });
  }

  commandForm.addEventListener('submit', function (event) {
    event.preventDefault();
    runCommand();
  });

  updateCounter();
})();
