/* 拆解树与发布（A3 /goal/tree）页面交互：树的展开收起、定位高亮、发布确认、承接会签、档案卡抽屉 */
(function () {
  'use strict';

  var dataEl = document.getElementById('goal-tree-data');
  var root = document.getElementById('tree-root');
  if (!dataEl || !root) { return; }
  var DATA = JSON.parse(dataEl.textContent);

  function $(id) { return document.getElementById(id); }

  function idemKey() {
    if (window.crypto && window.crypto.randomUUID) { return window.crypto.randomUUID(); }
    return 'k-' + Date.now() + '-' + Math.random().toString(16).slice(2);
  }

  function toast(message, isError) {
    var el = document.createElement('div');
    el.className = 'page-toast' + (isError ? ' is-error' : '');
    el.setAttribute('role', 'status');
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add('is-out'); }, 2600);
    setTimeout(function () { el.remove(); }, 3100);
  }

  function post(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idemKey() },
      body: JSON.stringify(payload)
    }).then(function (response) {
      return response.json().catch(function () { return { code: 1006, message: '服务响应异常，请重试。' }; });
    }).then(function (body) {
      if (body.code !== 0) {
        var error = new Error(body.message || '操作失败');
        error.code = body.code;
        error.data = body.data || {};
        throw error;
      }
      return body;
    });
  }

  /* ---------------- 树：展开 / 收起 / 定位 ---------------- */
  function childList(li) {
    for (var i = 0; i < li.children.length; i += 1) {
      if (li.children[i].classList.contains('tree-children')) { return li.children[i]; }
    }
    return null;
  }

  function toggleOf(li) {
    var card = null;
    for (var i = 0; i < li.children.length; i += 1) {
      if (li.children[i].classList.contains('node-card')) { card = li.children[i]; }
    }
    return card ? card.querySelector('.node-toggle') : null;
  }

  function setCollapsed(li, collapsed) {
    if (!childList(li)) { return; }
    li.classList.toggle('is-collapsed', collapsed);
    var toggle = toggleOf(li);
    if (toggle && !toggle.classList.contains('is-leaf')) {
      toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }
  }

  function allNodes() {
    return Array.prototype.slice.call(root.querySelectorAll('.tree-node'));
  }

  function depthOf(li) {
    return parseInt(li.dataset.depth || '0', 10) || 0;
  }

  function applyDefaultDepth(two) {
    allNodes().forEach(function (li) {
      if (!childList(li)) { return; }
      setCollapsed(li, two && depthOf(li) >= 2);
    });
  }

  root.addEventListener('click', function (event) {
    var toggle = event.target.closest('.node-toggle');
    if (!toggle || toggle.classList.contains('is-leaf')) { return; }
    var li = toggle.closest('.tree-node');
    if (li) { setCollapsed(li, !li.classList.contains('is-collapsed')); }
  });

  var expandAll = $('expand-all');
  if (expandAll) { expandAll.addEventListener('click', function () { applyDefaultDepth(false); }); }
  var collapseAll = $('collapse-all');
  if (collapseAll) { collapseAll.addEventListener('click', function () { applyDefaultDepth(true); }); }
  var expandTwo = $('expand-two');
  if (expandTwo) { expandTwo.addEventListener('click', function () { applyDefaultDepth(true); }); }

  function focusNode() {
    if (!DATA.focus_id) { return; }
    var target = null;
    allNodes().forEach(function (li) { if (li.dataset.id === DATA.focus_id) { target = li; } });
    if (!target) { return; }
    var cursor = target;
    while (cursor) {
      setCollapsed(cursor, false);
      cursor = cursor.parentElement && cursor.parentElement.closest
        ? cursor.parentElement.closest('.tree-node') : null;
    }
    target.classList.add('is-focus', 'is-pulse');
    target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    setTimeout(function () { target.classList.remove('is-pulse'); }, 2600);
  }

  /* ---------------- 动作分发 ---------------- */
  root.addEventListener('click', function (event) {
    var button = event.target.closest('[data-action]');
    if (!button) { return; }
    var action = button.dataset.action;
    var objectiveId = button.dataset.id;
    var card = button.closest('.node-card');
    var title = card ? (card.querySelector('.node-title') || {}).textContent || objectiveId : objectiveId;
    if (action === 'submit') { confirmSubmit(button, objectiveId, title); }
    else if (action === 'publish') { openPublish(button, objectiveId, title, !!childList(button.closest('.tree-node'))); }
    else if (action === 'confirm') { confirmAccept(button, objectiveId, title); }
    else if (action === 'reject') { openReject(objectiveId, title); }
    else if (action === 'detail') { openDrawer(objectiveId, title); }
  });

  function showBlockingModal(title, message, items) {
    var content = null;
    if (items && items.length) {
      content = document.createElement('ul');
      content.className = 'block-list';
      items.forEach(function (text) {
        var li = document.createElement('li');
        li.textContent = text;
        content.appendChild(li);
      });
    }
    window.NorthModal.open({ title: title, message: message, content: content, confirmText: '知道了' });
  }

  function confirmSubmit(button, objectiveId, title) {
    window.NorthModal.confirm({
      title: '提交确认',
      message: '确认将「' + title + '」提交给责任人进行承接会签？',
      impact: '提交后目标进入「待承接」，字段暂时冻结；驳回后会退回草稿。',
      confirmText: '提交确认'
    }).then(function (ok) {
      if (!ok) { return; }
      button.disabled = true;
      post(DATA.urls.submit, { objective_id: objectiveId, action: 'submit' }).then(function (body) {
        toast(body.message);
        setTimeout(function () { window.location.reload(); }, 700);
      }).catch(function (error) {
        button.disabled = false;
        if (error.code === 1005) {
          showBlockingModal('提交门禁未通过', error.message + '。可在 SMART 校验页采纳低分建议后重试。');
        } else {
          toast(error.message, true);
        }
      });
    });
  }

  function openPublish(button, objectiveId, title, hasChildren) {
    var scopeWrap = document.createElement('div');
    scopeWrap.className = 'publish-scope';

    var nodeOption = buildScopeOption('node', '仅发布本节点', '只发布「' + title + '」自身，其子目标保持原状态。', true);
    scopeWrap.appendChild(nodeOption.label);
    if (hasChildren) {
      var subtreeOption = buildScopeOption('subtree', '发布整棵子树', '本节点及全部已承接后代一并发布；存在未承接子目标时会整体阻断。', false);
      scopeWrap.appendChild(subtreeOption.label);
    }

    var noteWrap = document.createElement('label');
    noteWrap.className = 'modal-field';
    var noteLabel = document.createElement('span');
    noteLabel.textContent = '发布备注（可选，≤200 字，写入目标日志）';
    var note = document.createElement('textarea');
    note.maxLength = 200;
    note.placeholder = '例：季度重点目标，启动时向全员同步口径。';
    noteWrap.appendChild(noteLabel);
    noteWrap.appendChild(note);

    var content = document.createElement('div');
    content.appendChild(scopeWrap);
    content.appendChild(noteWrap);

    window.NorthModal.open({
      title: '发布确认',
      message: '发布后成功标准 / KR 基线 / 责任人 / 时限将快照留存，后续验收对照快照执行。',
      content: content,
      confirmText: '确认发布',
      autoFocus: 'textarea',
      onConfirm: function () {
        var scope = hasChildren && subtreeOption && subtreeOption.input.checked ? 'subtree' : 'node';
        button.disabled = true;
        return post(DATA.urls.publish, {
          objective_id: objectiveId,
          action: 'publish',
          publish_scope: scope,
          publish_note: note.value.trim()
        }).then(function (body) {
          toast(body.message);
          setTimeout(function () { window.location.reload(); }, 700);
          return true;
        }).catch(function (error) {
          button.disabled = false;
          if (error.code === 1005 && error.data.blocking) {
            showBlockingModal('发布被阻断', '存在未完成承接的子目标，请先提交并完成会签：',
              error.data.blocking.map(function (item) { return (item.title || item.objective_id) + '（' + (item.status_label || item.status) + '）'; }));
            return false;
          }
          if (error.code === 1005 && error.data.weight_issues) {
            showBlockingModal('权重合计异常', '同父兄弟权重合计须为 100% 才能发布：',
              error.data.weight_issues.map(function (item) { return (item.title || item.objective_id) + '：子目标合计 ' + item.sum + '%'; }));
            return false;
          }
          toast(error.message, true);
          return false;
        });
      }
    });
  }

  function buildScopeOption(value, label, hint, checked) {
    var wrap = document.createElement('label');
    var input = document.createElement('input');
    input.type = 'radio';
    input.name = 'publish-scope';
    input.value = value;
    input.checked = checked;
    var body = document.createElement('span');
    var strong = document.createElement('b');
    strong.textContent = label;
    var small = document.createElement('small');
    small.textContent = hint;
    body.appendChild(strong);
    body.appendChild(small);
    wrap.appendChild(input);
    wrap.appendChild(body);
    return { label: wrap, input: input };
  }

  function confirmAccept(button, objectiveId, title) {
    window.NorthModal.confirm({
      title: '确认承接',
      message: '确认承接「' + title + '」并在后续执行中负责达成？',
      impact: '确认后目标进入「已承接」，等待管理员发布；发布后字段调整需走变更申请。',
      confirmText: '确认承接'
    }).then(function (ok) {
      if (!ok) { return; }
      button.disabled = true;
      post(DATA.urls.countersign, { objective_id: objectiveId, action: 'confirm' }).then(function (body) {
        toast(body.message);
        setTimeout(function () { window.location.reload(); }, 700);
      }).catch(function (error) {
        button.disabled = false;
        toast(error.message, true);
      });
    });
  }

  function openReject(objectiveId, title) {
    var wrap = document.createElement('label');
    wrap.className = 'modal-field';
    var label = document.createElement('span');
    label.textContent = '驳回原因（必填，≤200 字）';
    var area = document.createElement('textarea');
    area.maxLength = 200;
    area.placeholder = '说明无法承接「' + title + '」的原因，将记入目标日志并退回草稿。';
    wrap.appendChild(label);
    wrap.appendChild(area);
    var error = document.createElement('p');
    error.className = 'modal-error';
    error.textContent = '请填写驳回原因。';
    error.hidden = true;
    var content = document.createElement('div');
    content.appendChild(wrap);
    content.appendChild(error);

    window.NorthModal.open({
      title: '驳回承接',
      content: content,
      confirmText: '确认驳回',
      danger: true,
      autoFocus: 'textarea',
      onConfirm: function () {
        var note = area.value.trim();
        if (!note) { error.hidden = false; area.focus(); return false; }
        return post(DATA.urls.countersign, { objective_id: objectiveId, action: 'reject', note: note }).then(function (body) {
          toast(body.message);
          setTimeout(function () { window.location.reload(); }, 700);
          return true;
        }).catch(function (err) {
          toast(err.message, true);
          return false;
        });
      }
    });
  }

  /* ---------------- 档案卡抽屉 ---------------- */
  var drawerOpen = false;

  function closeDrawer() {
    var mask = document.querySelector('.drawer-mask');
    var drawer = document.querySelector('.detail-drawer');
    if (mask) { mask.remove(); }
    if (drawer) { drawer.remove(); }
    document.body.classList.remove('modal-open');
    document.removeEventListener('keydown', onDrawerKey, true);
    drawerOpen = false;
  }

  function onDrawerKey(event) {
    if (event.key === 'Escape' && drawerOpen) {
      event.stopPropagation();
      event.preventDefault();
      closeDrawer();
    }
  }

  function node(className, text) {
    var el = document.createElement('div');
    if (className) { el.className = className; }
    if (text !== undefined) { el.textContent = text; }
    return el;
  }

  function item(parent, label, value) {
    var box = node('drawer-item');
    var span = document.createElement('span');
    span.textContent = label;
    var strong = document.createElement('strong');
    strong.textContent = value === undefined || value === null || value === '' ? '—' : String(value);
    box.appendChild(span);
    box.appendChild(strong);
    parent.appendChild(box);
  }

  function openDrawer(objectiveId, title) {
    if (drawerOpen) { closeDrawer(); }
    drawerOpen = true;

    var mask = document.createElement('div');
    mask.className = 'drawer-mask';
    mask.addEventListener('click', closeDrawer);

    var drawer = document.createElement('aside');
    drawer.className = 'detail-drawer';
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-modal', 'true');
    drawer.setAttribute('aria-label', '目标档案卡');

    var head = document.createElement('div');
    head.className = 'drawer-head';
    var heading = document.createElement('div');
    var h2 = document.createElement('h2');
    h2.textContent = title;
    var small = document.createElement('small');
    small.textContent = objectiveId + ' · 档案卡（只读聚合）';
    heading.appendChild(h2);
    heading.appendChild(small);
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'drawer-close';
    closeBtn.setAttribute('aria-label', '关闭档案卡');
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', closeDrawer);
    head.appendChild(heading);
    head.appendChild(closeBtn);

    var body = document.createElement('div');
    body.className = 'drawer-body';
    var loading = document.createElement('div');
    loading.className = 'drawer-loading';
    loading.textContent = '加载档案数据…';
    body.appendChild(loading);

    drawer.appendChild(head);
    drawer.appendChild(body);
    document.body.appendChild(mask);
    document.body.appendChild(drawer);
    document.body.classList.add('modal-open');
    document.addEventListener('keydown', onDrawerKey, true);
    closeBtn.focus();

    fetch(DATA.urls.detail + '?objective_id=' + encodeURIComponent(objectiveId), {
      headers: { 'Accept': 'application/json' }
    }).then(function (response) {
      return response.json().catch(function () { return { code: 1006, message: '服务响应异常' }; });
    }).then(function (payload) {
      if (payload.code !== 0) { throw new Error(payload.message || '加载失败'); }
      body.textContent = '';
      renderDrawer(body, payload.data);
    }).catch(function (error) {
      body.textContent = '';
      var fail = document.createElement('p');
      fail.className = 'drawer-empty';
      fail.textContent = error.message || '档案加载失败，请重试。';
      body.appendChild(fail);
    });
  }

  function renderDrawer(body, detail) {
    var objective = detail.objective || {};

    var baseSection = node('drawer-section');
    var baseTitle = document.createElement('h3');
    baseTitle.textContent = '档案';
    var grid = node('drawer-grid');
    item(grid, '状态', objective.status_label);
    item(grid, '层级', objective.level_label);
    item(grid, '归属组织', objective.org_name);
    item(grid, '责任人', objective.owner_name);
    item(grid, '权重', (objective.weight || 0) + '%');
    item(grid, '进度', (objective.progress || 0) + '%');
    item(grid, '计划窗口', (objective.plan_start || '—') + ' ~ ' + (objective.plan_end || '—'));
    item(grid, '最近更新', (objective.updated_at || '').replace('T', ' ').slice(0, 16));
    baseSection.appendChild(baseTitle);
    baseSection.appendChild(grid);
    body.appendChild(baseSection);

    var criteriaSection = node('drawer-section');
    var criteriaTitle = document.createElement('h3');
    criteriaTitle.textContent = '成功标准（发布快照对象）';
    criteriaSection.appendChild(criteriaTitle);
    if ((objective.criteria || []).length) {
      var criteriaList = document.createElement('ul');
      criteriaList.className = 'criteria-list-plain';
      objective.criteria.forEach(function (text) {
        var li = document.createElement('li');
        li.textContent = text;
        criteriaList.appendChild(li);
      });
      criteriaSection.appendChild(criteriaList);
    } else {
      var criteriaEmpty = document.createElement('p');
      criteriaEmpty.className = 'drawer-empty';
      criteriaEmpty.textContent = '未填写成功标准。';
      criteriaSection.appendChild(criteriaEmpty);
    }
    if (objective.snapshot) {
      var snap = document.createElement('p');
      snap.className = 'drawer-empty';
      snap.textContent = '发布快照：' + String(objective.snapshot.published_at || '').replace('T', ' ').slice(0, 16)
        + ' · 快照内成功标准 ' + ((objective.snapshot.criteria || []).length) + ' 条 · KR ' + ((objective.snapshot.krs || []).length) + ' 项';
      criteriaSection.appendChild(snap);
    }
    body.appendChild(criteriaSection);

    var krSection = node('drawer-section');
    var krTitle = document.createElement('h3');
    krTitle.textContent = 'KR 关键结果';
    krSection.appendChild(krTitle);
    if ((detail.krs || []).length) {
      var table = document.createElement('table');
      table.className = 'kr-table';
      var thead = document.createElement('thead');
      var headRow = document.createElement('tr');
      ['KR', '基线 → 目标', '当前', '权重'].forEach(function (text) {
        var th = document.createElement('th');
        th.textContent = text;
        headRow.appendChild(th);
      });
      thead.appendChild(headRow);
      table.appendChild(thead);
      var tbody = document.createElement('tbody');
      detail.krs.forEach(function (kr) {
        var row = document.createElement('tr');
        var cells = [
          kr.kr_name,
          String(kr.baseline_value) + ' → ' + String(kr.target_value) + (kr.unit ? ' ' + kr.unit : ''),
          String(kr.current_value === undefined ? '—' : kr.current_value) + (kr.unit ? ' ' + kr.unit : ''),
          (kr.weight === undefined ? '—' : kr.weight + '%')
        ];
        cells.forEach(function (text) {
          var td = document.createElement('td');
          td.textContent = text;
          row.appendChild(td);
        });
        tbody.appendChild(row);
      });
      table.appendChild(tbody);
      krSection.appendChild(table);
    } else {
      var krEmpty = document.createElement('p');
      krEmpty.className = 'drawer-empty';
      krEmpty.textContent = '暂无 KR（发布快照同样为空）。';
      krSection.appendChild(krEmpty);
    }
    body.appendChild(krSection);

    var logSection = node('drawer-section');
    var logTitle = document.createElement('h3');
    logTitle.textContent = '进度日志（近 20 条）';
    logSection.appendChild(logTitle);
    if ((detail.logs || []).length) {
      var logList = document.createElement('ul');
      logList.className = 'drawer-list';
      detail.logs.forEach(function (log) {
        var li = document.createElement('li');
        var note = document.createElement('b');
        note.textContent = log.note;
        var meta = document.createElement('small');
        meta.textContent = (log.operator_name || '—') + ' · ' + (log.logged_at || '') + (log.progress === null || log.progress === undefined ? '' : ' · 进度 ' + log.progress + '%');
        li.appendChild(note);
        li.appendChild(meta);
        logList.appendChild(li);
      });
      logSection.appendChild(logList);
    } else {
      var logEmpty = document.createElement('p');
      logEmpty.className = 'drawer-empty';
      logEmpty.textContent = '暂无进度日志。';
      logSection.appendChild(logEmpty);
    }
    body.appendChild(logSection);

    var changeSection = node('drawer-section');
    var changeTitle = document.createElement('h3');
    changeTitle.textContent = '变更记录';
    changeSection.appendChild(changeTitle);
    if ((detail.changes || []).length) {
      var changeList = document.createElement('ul');
      changeList.className = 'drawer-list';
      detail.changes.forEach(function (change) {
        var li = document.createElement('li');
        li.textContent = change.change_type + ' · ' + change.status_label + (change.reason_code ? ' · ' + change.reason_code : '');
        var meta = document.createElement('small');
        meta.textContent = '申请时间 ' + (change.applied_at || '—');
        li.appendChild(meta);
        changeList.appendChild(li);
      });
      changeSection.appendChild(changeList);
    } else {
      var changeEmpty = document.createElement('p');
      changeEmpty.className = 'drawer-empty';
      changeEmpty.textContent = '暂无变更申请。';
      changeSection.appendChild(changeEmpty);
    }
    body.appendChild(changeSection);

    var assessSection = node('drawer-section');
    var assessTitle = document.createElement('h3');
    assessTitle.textContent = '考评记录';
    assessSection.appendChild(assessTitle);
    if ((detail.assessments || []).length) {
      var assessList = document.createElement('ul');
      assessList.className = 'drawer-list';
      detail.assessments.forEach(function (item) {
        var li = document.createElement('li');
        li.textContent = '终评 ' + (item.final_score === undefined || item.final_score === null ? '—' : item.final_score)
          + ' · 等级 ' + (item.grade || '—') + ' · ' + item.status_label;
        var meta = document.createElement('small');
        meta.textContent = '自评 ' + (item.self_score === undefined || item.self_score === null ? '—' : item.self_score)
          + (item.conclusion ? ' · 结论：' + item.conclusion : '');
        li.appendChild(meta);
        assessList.appendChild(li);
      });
      assessSection.appendChild(assessList);
    } else {
      var assessEmpty = document.createElement('p');
      assessEmpty.className = 'drawer-empty';
      assessEmpty.textContent = '暂无考评记录。';
      assessSection.appendChild(assessEmpty);
    }
    body.appendChild(assessSection);
  }

  /* ---------------- 初始化 ---------------- */
  applyDefaultDepth(true);
  focusNode();
})();
