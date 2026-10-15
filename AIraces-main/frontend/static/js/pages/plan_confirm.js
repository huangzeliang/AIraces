/* 提炼确认（A2）页面脚本：待办增删 / 目标与待办校验 / 确认按钮联动 / todos 序列化提交。
   零依赖实现；删除按钮在仅剩 1 条待办时禁用（TC-007）。 */

(function () {
  'use strict';

  var form = document.querySelector('#confirm-form');
  if (!form) {
    return;
  }

  var goalInput = document.querySelector('#goal-input');
  var goalError = document.querySelector('#goal-error');
  var todoList = document.querySelector('#todo-list');
  var todosError = document.querySelector('#todos-error');
  var todoCount = document.querySelector('#todo-count');
  var todosJson = document.querySelector('#todos-json');
  var addButton = document.querySelector('#add-todo');
  var confirmButton = document.querySelector('#confirm-button');
  var confirmHint = document.querySelector('#confirm-hint');

  function rows() {
    return Array.prototype.slice.call(todoList.querySelectorAll('.todo-row'));
  }

  function reindex() {
    rows().forEach(function (row, index) {
      row.querySelector('.todo-index').textContent = String(index + 1);
    });
  }

  function refreshState() {
    var list = rows();
    var goalOk = goalInput.value.trim().length > 0;
    goalInput.classList.toggle('is-invalid', !goalOk);
    goalError.hidden = goalOk;

    var validRows = list.filter(function (row) {
      return row.querySelector('.todo-content').value.trim().length > 0;
    });
    var removeDisabled = list.length <= 1;
    list.forEach(function (row) {
      row.querySelector('.todo-remove').disabled = removeDisabled;
      row.querySelector('.todo-content').classList.toggle(
        'is-invalid', row.querySelector('.todo-content').value.trim().length === 0);
    });

    var todosOk = validRows.length > 0;
    todosError.hidden = todosOk;
    if (todoCount) {
      todoCount.textContent = list.length + ' 条';
    }
    confirmButton.disabled = !(goalOk && todosOk);
    if (!goalOk) {
      confirmHint.textContent = '目标不能为空，请先补充。';
    } else if (!todosOk) {
      confirmHint.textContent = '至少保留一条待办。';
    } else {
      confirmHint.textContent = '校验通过：' + validRows.length + ' 条待办将随确认入库。';
    }
  }

  function buildRow(content, timeValue, todoId) {
    var row = document.createElement('div');
    row.className = 'todo-row';
    if (todoId) {
      row.setAttribute('data-todo-id', todoId);
    }

    var indexTag = document.createElement('span');
    indexTag.className = 'todo-index';
    indexTag.setAttribute('aria-hidden', 'true');

    var fields = document.createElement('div');
    fields.className = 'todo-fields';

    var contentInput = document.createElement('input');
    contentInput.className = 'todo-content';
    contentInput.maxLength = 120;
    contentInput.placeholder = '待办内容';
    contentInput.setAttribute('aria-label', '待办内容');
    contentInput.value = content || '';

    var timeInput = document.createElement('input');
    timeInput.className = 'todo-time';
    timeInput.type = 'datetime-local';
    timeInput.setAttribute('aria-label', '关键时间');
    timeInput.value = timeValue || '';

    fields.appendChild(contentInput);
    fields.appendChild(timeInput);

    var remove = document.createElement('button');
    remove.className = 'todo-remove';
    remove.type = 'button';
    remove.setAttribute('aria-label', '删除该待办');
    remove.title = '删除该待办';
    remove.textContent = '✕';

    row.appendChild(indexTag);
    row.appendChild(fields);
    row.appendChild(remove);
    return row;
  }

  addButton.addEventListener('click', function () {
    var row = buildRow('', '', '');
    todoList.appendChild(row);
    reindex();
    refreshState();
    row.querySelector('.todo-content').focus();
  });

  todoList.addEventListener('click', function (event) {
    var button = event.target.closest('.todo-remove');
    if (!button || button.disabled) {
      return;
    }
    var row = button.closest('.todo-row');
    if (row) {
      row.remove();
      reindex();
      refreshState();
    }
  });

  todoList.addEventListener('input', refreshState);
  goalInput.addEventListener('input', refreshState);

  form.addEventListener('submit', function (event) {
    refreshState();
    if (confirmButton.disabled) {
      event.preventDefault();
      if (!goalInput.value.trim()) {
        goalInput.focus();
      } else {
        var firstEmpty = rows().find(function (row) {
          return !row.querySelector('.todo-content').value.trim();
        });
        (firstEmpty ? firstEmpty.querySelector('.todo-content') : goalInput).focus();
      }
      return;
    }

    var payload = [];
    rows().forEach(function (row) {
      var content = row.querySelector('.todo-content').value.trim();
      if (!content) {
        return;
      }
      var time = row.querySelector('.todo-time').value;
      if (time && time.length === 16) {
        time += ':00';
      }
      payload.push({
        todo_id: row.getAttribute('data-todo-id') || '',
        content: content,
        key_time: time || ''
      });
    });
    todosJson.value = JSON.stringify(payload);
    confirmButton.disabled = true;
    confirmButton.textContent = '生成中…';
  });

  window.addEventListener('pageshow', function () {
    confirmButton.textContent = '确认并生成排期';
    refreshState();
  });

  refreshState();
})();
