/* 智能排期（A3）页面脚本：时间条拖拽 / 截止游标拖拽 / 键盘微调 / 冲突实时重算 / 预计耗时与优先级编辑 / 保存校验。
   统一日程表：全部计划的任务汇总在一张时间轴上；冲突口径与后端一致（跨计划时间重叠一并判定，阻断保存）。
   日期时间轴吸顶：页面下滚时保持可见，横向滚动与行轨道同步。
   零依赖实现；时间序列化使用本地时间拼接，避免 toISOString 的时区偏移。 */

(function () {
  'use strict';

  var payload = document.querySelector('#schedule-data');
  var rowsHost = document.querySelector('#timeline-rows');
  if (!payload || !rowsHost) {
    return;
  }

  var motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  var prefersReduced = motionQuery.matches;

  var data = JSON.parse(payload.textContent);
  var saveForm = document.querySelector('#save-form');
  var saveButton = document.querySelector('#save-button');
  var banner = document.querySelector('#conflict-banner');
  var dirtyChip = document.querySelector('#dirty-chip');

  var spanStart = new Date(data.span.start);
  var spanEnd = new Date(data.span.end);
  var totalMs = Math.max(1, spanEnd - spanStart);
  var snapMs = 15 * 60000;

  var tasks = data.tasks.map(function (item) {
    return {
      task_id: item.task_id,
      title: item.title,
      plan_id: item.plan_id || '',
      plan_title: item.plan_title || '',
      status: item.status,
      priority: item.priority,
      estimate_minutes: item.estimate_minutes,
      start: new Date(item.start),
      finish: new Date(item.finish),
      due: item.due_at ? new Date(item.due_at) : null,
      depends_on: item.depends_on || [],
      conflicts: [],
      overdue: false
    };
  });

  var rowEls = {};
  rowsHost.querySelectorAll('.timeline-item').forEach(function (el) {
    rowEls[el.getAttribute('data-task-id')] = el;
  });

  var dirty = false;
  var invalidEstimates = new Set();
  var dragging = null;
  var dueDrag = null;

  /* ---------------- 工具 ---------------- */
  function pad(number) {
    return number < 10 ? '0' + number : String(number);
  }

  function fmt(moment) {
    return pad(moment.getMonth() + 1) + '-' + pad(moment.getDate()) + ' ' +
      pad(moment.getHours()) + ':' + pad(moment.getMinutes());
  }

  function toIso(moment) {
    return moment.getFullYear() + '-' + pad(moment.getMonth() + 1) + '-' + pad(moment.getDate()) +
      'T' + pad(moment.getHours()) + ':' + pad(moment.getMinutes()) + ':00';
  }

  function position(moment) {
    return Math.max(0, Math.min(100, (moment - spanStart) / totalMs * 100));
  }

  function overlaps(a, b) {
    return a.start < b.finish && b.start < a.finish;
  }

  function markDirty() {
    dirty = true;
    if (dirtyChip) {
      dirtyChip.hidden = false;
    }
  }

  function showBanner(message, success) {
    banner.hidden = false;
    banner.classList.toggle('is-success', Boolean(success));
    banner.textContent = message;
  }

  function hideBanner() {
    banner.hidden = true;
    banner.classList.remove('is-success');
  }

  /* ---------------- 冲突重算 ---------------- */
  function otherLabel(other, self) {
    if (other.plan_id && self.plan_id && other.plan_id !== self.plan_id && other.plan_title) {
      return '与其他计划「' + other.plan_title.slice(0, 14) + '」的「' + other.title + '」';
    }
    return '与「' + other.title + '」';
  }

  function recompute() {
    tasks.forEach(function (task) {
      task.conflicts = [];
      task.overdue = Boolean(task.due && task.finish > task.due);
    });

    for (var i = 0; i < tasks.length; i += 1) {
      for (var j = i + 1; j < tasks.length; j += 1) {
        if (overlaps(tasks[i], tasks[j])) {
          tasks[i].conflicts.push(otherLabel(tasks[j], tasks[i]) + '时间重叠');
          tasks[j].conflicts.push(otherLabel(tasks[i], tasks[j]) + '时间重叠');
        }
      }
    }

    var index = {};
    tasks.forEach(function (task) { index[task.task_id] = task; });
    tasks.forEach(function (task) {
      task.depends_on.forEach(function (depId) {
        var other = index[depId];
        if (other && other.finish > task.start) {
          task.conflicts.push('依赖「' + other.title + '」未满足（其计划完成 ' + fmt(other.finish) + '）');
        }
      });
    });

    render();
  }

  /* ---------------- 渲染 ---------------- */
  function ensureFlag(el, className, text, title) {
    var flags = el.querySelector('.row-flags');
    var node = flags.querySelector('.' + className);
    if (!text) {
      if (node) {
        node.remove();
      }
      return;
    }
    if (!node) {
      node = document.createElement('span');
      node.className = 'flag ' + className;
      flags.insertBefore(node, flags.firstChild);
    }
    node.textContent = text;
    node.title = title || '';
  }

  function render() {
    var conflictCount = 0;
    var overdueCount = 0;

    tasks.forEach(function (task) {
      var el = rowEls[task.task_id];
      if (!el) {
        return;
      }
      var hasConflict = task.conflicts.length > 0;
      if (hasConflict) {
        conflictCount += 1;
      }
      if (task.overdue) {
        overdueCount += 1;
      }

      el.classList.toggle('conflict', hasConflict);
      el.classList.toggle('overdue', task.overdue);
      el.setAttribute('data-conflict', hasConflict ? 'true' : 'false');
      el.setAttribute('data-overdue', task.overdue ? 'true' : 'false');

      ensureFlag(el, 'flag-conflict', hasConflict ? '⚠ 冲突' : '', task.conflicts.join('；'));
      ensureFlag(el, 'flag-overdue', task.overdue ? '超期' : '', '计划完成时间点晚于截止时间');

      var startNode = el.querySelector('.row-start');
      var finishNode = el.querySelector('.row-finish');
      if (startNode) {
        startNode.textContent = fmt(task.start);
      }
      if (finishNode) {
        finishNode.textContent = fmt(task.finish);
      }

      var bar = el.querySelector('.time-bar');
      if (bar) {
        bar.style.left = position(task.start).toFixed(4) + '%';
        bar.style.width = Math.max(0.4, position(task.finish) - position(task.start)).toFixed(4) + '%';
        bar.setAttribute('aria-label', task.title + '：' + fmt(task.start) + ' 至 ' + fmt(task.finish) + '，可拖拽调整');
        bar.title = task.title + ' · ' + fmt(task.start) + ' → ' + fmt(task.finish);
      }

      var marker = el.querySelector('.due-marker');
      if (marker && task.due) {
        marker.style.left = position(task.due).toFixed(4) + '%';
        marker.title = '截止时间 ' + fmt(task.due) + '，可拖拽调整';
        marker.setAttribute('aria-label', task.title + '的截止时间 ' + fmt(task.due) + '，可拖拽或使用左右方向键调整');
      }
      var dueText = el.querySelector('.due-text');
      if (dueText && task.due) {
        dueText.textContent = '截止 ' + fmt(task.due);
      }
    });

    var statConflicts = document.querySelector('#stat-conflicts');
    var statOverdue = document.querySelector('#stat-overdue');
    if (statConflicts) {
      statConflicts.textContent = String(conflictCount);
    }
    if (statOverdue) {
      statOverdue.textContent = String(overdueCount);
    }

    if (conflictCount) {
      showBanner('存在 ' + conflictCount + ' 处未处理的冲突（时间重叠 / 依赖未满足），请调整后再保存。');
    } else if (!banner.classList.contains('is-success')) {
      hideBanner();
    }
  }

  /* ---------------- 拖拽与键盘调整 ---------------- */
  function applyStart(task, newStart) {
    task.start = newStart;
    task.finish = new Date(newStart.getTime() + task.estimate_minutes * 60000);
    markDirty();
    recompute();
  }

  function attachBar(bar, task) {
    bar.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) {
        return;
      }
      event.preventDefault();
      var track = bar.parentElement;
      dragging = {
        task: task,
        bar: bar,
        startX: event.clientX,
        origStart: new Date(task.start),
        pxPerMs: (track.clientWidth || 1) / totalMs,
        pointerId: event.pointerId
      };
      if (bar.setPointerCapture) {
        bar.setPointerCapture(event.pointerId);
      }
      bar.classList.add('dragging');
    });

    bar.addEventListener('pointermove', function (event) {
      if (!dragging || dragging.bar !== bar) {
        return;
      }
      var deltaMs = (event.clientX - dragging.startX) / dragging.pxPerMs;
      var snapped = Math.round((dragging.origStart.getTime() + deltaMs) / snapMs) * snapMs;
      applyStart(task, new Date(snapped));
    });

    function endDrag() {
      if (!dragging || dragging.bar !== bar) {
        return;
      }
      bar.classList.remove('dragging');
      dragging = null;
      recompute();
    }
    bar.addEventListener('pointerup', endDrag);
    bar.addEventListener('pointercancel', endDrag);

    bar.addEventListener('keydown', function (event) {
      var step = 0;
      if (event.key === 'ArrowLeft') {
        step = -snapMs;
      } else if (event.key === 'ArrowRight') {
        step = snapMs;
      } else {
        return;
      }
      event.preventDefault();
      applyStart(task, new Date(task.start.getTime() + step));
    });
  }

  /* ---------------- 截止游标拖拽（调整截止时间） ---------------- */
  function applyDue(task, newDue) {
    task.due = newDue;
    markDirty();
    recompute();
  }

  function attachDueMarker(marker, task) {
    if (!task.due) {
      return;
    }

    marker.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) {
        return;
      }
      event.preventDefault();
      var track = marker.parentElement;
      dueDrag = {
        task: task,
        marker: marker,
        startX: event.clientX,
        origDue: new Date(task.due),
        pxPerMs: (track.clientWidth || 1) / totalMs,
        pointerId: event.pointerId
      };
      if (marker.setPointerCapture) {
        marker.setPointerCapture(event.pointerId);
      }
      marker.classList.add('dragging');
    });

    marker.addEventListener('pointermove', function (event) {
      if (!dueDrag || dueDrag.marker !== marker) {
        return;
      }
      var deltaMs = (event.clientX - dueDrag.startX) / dueDrag.pxPerMs;
      var snapped = Math.round((dueDrag.origDue.getTime() + deltaMs) / snapMs) * snapMs;
      snapped = Math.max(spanStart.getTime(), Math.min(spanEnd.getTime(), snapped));
      applyDue(task, new Date(snapped));
    });

    function endDrag() {
      if (!dueDrag || dueDrag.marker !== marker) {
        return;
      }
      marker.classList.remove('dragging');
      dueDrag = null;
      recompute();
    }
    marker.addEventListener('pointerup', endDrag);
    marker.addEventListener('pointercancel', endDrag);

    marker.addEventListener('keydown', function (event) {
      var step = 0;
      if (event.key === 'ArrowLeft') {
        step = -snapMs;
      } else if (event.key === 'ArrowRight') {
        step = snapMs;
      } else {
        return;
      }
      event.preventDefault();
      var next = task.due.getTime() + step;
      next = Math.max(spanStart.getTime(), Math.min(spanEnd.getTime(), next));
      applyDue(task, new Date(next));
    });
  }

  /* ---------------- 行内编辑 ---------------- */
  function setupRowControls() {
    tasks.forEach(function (task) {
      var el = rowEls[task.task_id];
      if (!el) {
        return;
      }
      var bar = el.querySelector('.time-bar');
      if (bar) {
        attachBar(bar, task);
      }

      var marker = el.querySelector('.due-marker');
      if (marker) {
        attachDueMarker(marker, task);
      }

      var estimateInput = el.querySelector('.row-estimate');
      if (estimateInput) {
        estimateInput.addEventListener('input', function () {
          var raw = estimateInput.value.trim();
          var value = /^\d+$/.test(raw) ? parseInt(raw, 10) : NaN;
          if (!value || value <= 0) {
            estimateInput.classList.add('is-invalid');
            invalidEstimates.add(task.task_id);
            return;
          }
          estimateInput.classList.remove('is-invalid');
          invalidEstimates.delete(task.task_id);
          task.estimate_minutes = value;
          task.finish = new Date(task.start.getTime() + value * 60000);
          markDirty();
          recompute();
        });
      }

      var prioritySelect = el.querySelector('.row-priority');
      if (prioritySelect) {
        prioritySelect.addEventListener('change', function () {
          task.priority = prioritySelect.value;
          markDirty();
        });
      }
    });
  }

  /* ---------------- 保存 ---------------- */
  saveForm.addEventListener('submit', function (event) {
    event.preventDefault();
    if (!tasks.length) {
      return;
    }
    if (invalidEstimates.size) {
      showBanner('预计耗时必须为大于 0 的整数，请修正后再保存。');
      var first = document.querySelector('.row-estimate.is-invalid');
      if (first) {
        first.focus();
      }
      return;
    }
    var conflictCount = tasks.filter(function (task) { return task.conflicts.length > 0; }).length;
    if (conflictCount) {
      showBanner('存在 ' + conflictCount + ' 处未处理的冲突（时间重叠 / 依赖未满足），请调整后再保存。');
      var panel = document.querySelector('.timeline-panel');
      if (panel) {
        panel.scrollIntoView({ behavior: prefersReduced ? 'auto' : 'smooth', block: 'start' });
      }
      return;
    }

    saveButton.disabled = true;
    saveButton.textContent = '保存中…';
    fetch(saveForm.getAttribute('action'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        items: tasks.map(function (task) {
          return {
            task_id: task.task_id,
            scheduled_start_at: toIso(task.start),
            estimate_minutes: task.estimate_minutes,
            priority: task.priority,
            due_at: task.due ? toIso(task.due) : ''
          };
        })
      })
    }).then(function (response) {
      return response.json().then(function (body) {
        return { ok: response.ok, body: body };
      });
    }).then(function (result) {
      if (result.ok && result.body.ok) {
        dirty = false;
        showBanner(result.body.message || '排期已保存。', true);
        saveButton.textContent = '已保存 ✓';
        window.setTimeout(function () { window.location.reload(); }, 700);
      } else {
        saveButton.disabled = false;
        saveButton.textContent = '保存排期';
        showBanner((result.body && result.body.message) || '保存失败，请重试。');
      }
    }).catch(function () {
      saveButton.disabled = false;
      saveButton.textContent = '保存排期';
      showBanner('网络异常，保存失败，请重试。');
    });
  });

  /* ---------------- 重排建议忽略（持久化：刷新后不再显示） ---------------- */
  document.querySelectorAll('.suggestion-ignore').forEach(function (button) {
    button.addEventListener('click', function () {
      var card = button.closest('.suggestion-card');
      if (!card) {
        return;
      }
      button.disabled = true;
      fetch('/schedule/suggest/ignore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: card.getAttribute('data-task-id') || '',
          start: card.getAttribute('data-suggest-start') || ''
        })
      }).then(function (response) {
        return response.json();
      }).then(function (result) {
        if (result && result.ok) {
          card.remove();
          var panel = document.querySelector('#suggestions-panel');
          if (panel && !panel.querySelector('.suggestion-card')) {
            panel.remove();
          }
        } else {
          button.disabled = false;
        }
      }).catch(function () {
        button.disabled = false;
      });
    });
  });

  /* ---------------- 日期时间轴吸顶：与行轨道横向滚动同步 ---------------- */
  var scrollHost = document.querySelector('#timeline-scroll');
  var rulerTrack = document.querySelector('#ruler-track');
  if (scrollHost && rulerTrack) {
    var syncRuler = function () {
      rulerTrack.style.transform = 'translateX(' + (-scrollHost.scrollLeft) + 'px)';
    };
    scrollHost.addEventListener('scroll', syncRuler);
    window.addEventListener('resize', syncRuler);
  }

  /* ---------------- 初始化 ---------------- */
  var rulerLabels = document.querySelectorAll('.ruler-day');
  if (rulerLabels.length > 14) {
    rulerLabels.forEach(function (label, index) {
      if (index % 2 === 1) {
        label.style.display = 'none';
      }
    });
  }

  setupRowControls();
  recompute();

  motionQuery.addEventListener('change', function (event) {
    prefersReduced = event.matches;
  });
})();
