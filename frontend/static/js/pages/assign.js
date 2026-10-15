/* 智能派工（A4 /assign）页面交互：饱和度试算、AI 分析、计划项、强制下发、员工档案 */
(function () {
  'use strict';

  var dataEl = document.getElementById('assign-data');
  if (!dataEl) { return; }
  var DATA = JSON.parse(dataEl.textContent);
  var TASK = DATA.selected && DATA.selected.task ? DATA.selected.task : null;
  var OBJECTIVE = DATA.selected ? DATA.selected.objective : null;
  var TH = DATA.thresholds || { green: 90, red: 110, limit: 90 };

  var state = {
    planDraft: null,        // 计划项草案（null = 由服务端按权重生成）
    planSignature: ''       // 草案生成时的选择签名（权重变化后作废）
  };

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

  function get(url) {
    return fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (response) {
      return response.json().catch(function () { return { code: 1006, message: '服务响应异常，请重试。' }; });
    }).then(function (body) {
      if (body.code !== 0) {
        var error = new Error(body.message || '加载失败');
        error.code = body.code;
        throw error;
      }
      return body;
    });
  }

  function round1(value) { return Math.round(value * 10) / 10; }

  function levelOf(saturation) {
    if (saturation <= TH.green) { return 'green'; }
    if (saturation <= TH.red) { return 'yellow'; }
    return 'red';
  }

  function adviceText(level, after) {
    if (level === 'green') { return '推荐：加入后饱和度 ' + after + '%，处于健康区间（≤ ' + TH.green + '%）。'; }
    if (level === 'yellow') { return '谨慎：加入后饱和度 ' + after + '%，接近上限（> ' + TH.green + '%），请确认时间冲突。'; }
    return '不建议：加入后饱和度 ' + after + '%，超过 ' + TH.red + '% 上限，提交将被拦截（需强制下发）。';
  }

  /* ---------------- 选择与实时试算 ---------------- */
  var memberList = $('member-list');

  function memberRows() {
    return memberList ? Array.prototype.slice.call(memberList.querySelectorAll('.member-row')) : [];
  }

  function pickState() {
    var picked = [];
    memberRows().forEach(function (row) {
      var box = row.querySelector('[data-role="pick"]');
      if (box && box.checked) {
        var weightInput = row.querySelector('[data-role="weight"]');
        var ownerRadio = row.querySelector('[data-role="owner"]');
        picked.push({
          user_id: row.dataset.userId,
          weight: Number(weightInput.value || 0),
          is_owner: !!(ownerRadio && ownerRadio.checked)
        });
      }
    });
    return picked;
  }

  function ensureOwner() {
    var pickedRows = [];
    var hasOwner = false;
    memberRows().forEach(function (row) {
      var box = row.querySelector('[data-role="pick"]');
      var radio = row.querySelector('[data-role="owner"]');
      if (!radio) { return; }
      var picked = !!(box && box.checked);
      radio.disabled = !picked;
      if (!picked && radio.checked) { radio.checked = false; }
      if (picked) {
        pickedRows.push(radio);
        if (radio.checked) { hasOwner = true; }
      }
    });
    if (!hasOwner && pickedRows.length) { pickedRows[0].checked = true; }
  }

  function signature(picked) {
    return picked.map(function (item) { return item.user_id + ':' + item.weight; }).sort().join('|');
  }

  function updateRow(row) {
    var box = row.querySelector('[data-role="pick"]');
    var weightInput = row.querySelector('[data-role="weight"]');
    var chip = row.querySelector('[data-role="level-chip"]');
    var barAdd = row.querySelector('[data-role="bar-add"]');
    var advice = row.querySelector('[data-role="advice"]');
    var load = Number(row.dataset.load || 0);
    var base = Number(row.dataset.base || 480) || 480;
    var hours = Number(TASK ? TASK.plan_hours : 0);
    var before = round1(load / base * 100);
    var picked = !!(box && box.checked);
    var weight = Number(weightInput.value || 0);
    weightInput.disabled = !picked;
    var after = picked && weight > 0 ? round1((load + hours * weight / 100) / base * 100) : before;
    row.classList.toggle('is-picked', picked);
    row.classList.remove('level-green', 'level-yellow', 'level-red');
    row.classList.add('level-' + levelOf(picked ? after : before));
    if (chip) {
      chip.className = 'level-chip level-' + levelOf(picked ? after : before);
      chip.textContent = picked ? before + '% → ' + after + '%' : '饱和度 ' + before + '%';
    }
    if (barAdd) {
      if (picked && after > before) {
        var baseWidth = Math.min(100, before);
        barAdd.style.display = 'block';
        barAdd.style.left = baseWidth + '%';
        barAdd.style.width = (Math.min(100, after) - baseWidth) + '%';
      } else {
        barAdd.style.display = 'none';
      }
    }
    if (advice) { advice.textContent = adviceText(levelOf(picked ? after : before), picked ? after : before); }
  }

  function updateRecommend() {
    var strip = $('recommend-strip');
    var list = $('recommend-list');
    if (!strip || !list) { return; }
    var pickedIds = {};
    pickState().forEach(function (item) { pickedIds[item.user_id] = true; });
    var hours = Number(TASK ? TASK.plan_hours : 0);
    var candidates = [];
    memberRows().forEach(function (row) {
      if (pickedIds[row.dataset.userId]) { return; }
      var load = Number(row.dataset.load || 0);
      var base = Number(row.dataset.base || 480) || 480;
      var after = round1((load + hours) / base * 100);
      if (levelOf(after) === 'red') { return; }
      candidates.push({
        row: row, after: after, level: levelOf(after),
        name: (row.querySelector('.member-head strong') || {}).textContent || row.dataset.userId
      });
    });
    candidates.sort(function (a, b) { return a.after - b.after; });
    var top = candidates.slice(0, 2);
    list.textContent = '';
    if (!top.length) { strip.hidden = true; return; }
    top.forEach(function (item) {
      var chip = document.createElement('span');
      chip.className = 'recommend-chip';
      var text = document.createElement('span');
      text.textContent = item.name + ' · 加入后 ' + item.after + '%';
      var small = document.createElement('small');
      small.textContent = item.level === 'green' ? '健康' : '临界';
      var swap = document.createElement('button');
      swap.type = 'button';
      swap.className = 'swap-btn';
      swap.textContent = '换成 TA';
      swap.addEventListener('click', function () {
        memberRows().forEach(function (row) {
          var box = row.querySelector('[data-role="pick"]');
          if (box) { box.checked = false; }
        });
        var box = item.row.querySelector('[data-role="pick"]');
        if (box) { box.checked = true; }
        redistribute();
        syncAll();
      });
      chip.appendChild(text);
      chip.appendChild(small);
      chip.appendChild(swap);
      list.appendChild(chip);
    });
    strip.hidden = false;
  }

  function redistribute() {
    var picked = [];
    memberRows().forEach(function (row) {
      var box = row.querySelector('[data-role="pick"]');
      if (box && box.checked) { picked.push(row); }
    });
    if (!picked.length) { return; }
    if (!state.userTouchedWeight || picked.length !== state.lastCount) {
      var each = round1(100 / picked.length);
      var used = 0;
      picked.forEach(function (row, index) {
        var input = row.querySelector('[data-role="weight"]');
        if (index === picked.length - 1) {
          input.value = String(round1(100 - used));
        } else {
          input.value = String(each);
          used = round1(used + each);
        }
      });
      state.userTouchedWeight = false;
      state.lastCount = picked.length;
    }
  }

  function syncAll() {
    ensureOwner();
    memberRows().forEach(updateRow);
    var picked = pickState();
    var sum = round1(picked.reduce(function (acc, item) { return acc + item.weight; }, 0));
    var tip = $('weight-sum-tip');
    var commit = $('commit-button');
    var hint = $('commit-hint');
    var ok = picked.length > 0 && Math.abs(sum - 100) <= 0.1;
    if (tip) {
      tip.textContent = '已选 ' + picked.length + ' 人 · 权重合计 ' + sum + '%（须为 100%）';
      tip.className = 'dispatch-tip' + (picked.length === 0 ? '' : (ok ? ' is-ok' : ' is-bad'));
    }
    if (commit) { commit.disabled = !ok; }
    if (hint) {
      if (!picked.length) {
        hint.textContent = '勾选执行人后下发；红区成员会被拦截，可使用强制下发并填写理由。';
      } else if (!ok) {
        hint.textContent = '权重合计 ' + sum + '%，须调整为 100% 才能下发。';
      } else {
        var worst = null;
        memberRows().forEach(function (row) {
          var box = row.querySelector('[data-role="pick"]');
          if (!box || !box.checked) { return; }
          var load = Number(row.dataset.load || 0);
          var base = Number(row.dataset.base || 480) || 480;
          var after = round1((load + Number(TASK.plan_hours) * Number(row.querySelector('[data-role="weight"]').value) / 100) / base * 100);
          if (!worst || after > worst.after) {
            worst = { after: after, name: (row.querySelector('.member-head strong') || {}).textContent };
          }
        });
        if (worst) {
          hint.textContent = '预计最高饱和度：' + worst.name + ' ' + worst.after + '%' +
            (worst.after > TH.red ? '（超载，将触发强制下发流程）' : '（可下发）');
        }
      }
    }
    var sig = signature(picked);
    if (state.planDraft && state.planSignature && sig !== state.planSignature) {
      state.planDraft = null;
      state.planSignature = '';
      var draftBox = $('plan-draft');
      if (draftBox) { draftBox.hidden = true; }
      toast('权重已变化，重新生成或由系统按权重自动拆分计划项。');
    }
    updateRecommend();
  }

  if (memberList) {
    memberList.addEventListener('change', function (event) {
      var target = event.target;
      if (target.matches('[data-role="pick"]')) {
        redistribute();
        syncAll();
      } else if (target.matches('[data-role="weight"]')) {
        state.userTouchedWeight = true;
        syncAll();
      }
    });
    memberList.addEventListener('input', function (event) {
      if (event.target.matches('[data-role="weight"]')) {
        state.userTouchedWeight = true;
        syncAll();
      }
    });
  }

  /* ---------------- 重新试算（服务端口径） ---------------- */
  var recheck = $('recheck-button');
  if (recheck) {
    recheck.addEventListener('click', function () {
      var picked = pickState();
      recheck.disabled = true;
      post(DATA.urls.check, { task_id: TASK.task_id, assignees: picked }).then(function (body) {
        var data = body.data;
        var byId = {};
        (data.members || []).forEach(function (row) { byId[row.user_id] = row; });
        memberRows().forEach(function (row) {
          var fresh = byId[row.dataset.userId];
          if (!fresh) { return; }
          row.dataset.load = String(fresh.load_hours);
          row.dataset.base = String(fresh.base_hours);
          var box = row.querySelector('[data-role="pick"]');
          var weightInput = row.querySelector('[data-role="weight"]');
          if (box && weightInput) { box.checked = !!fresh.selected; }
        });
        syncAll();
        var parts = [];
        if (data.overload && data.overload.length) {
          parts.push('超载：' + data.overload.map(function (row) { return row.name + ' ' + row.saturation_after + '%'; }).join('、'));
        }
        if (data.warnings && data.warnings.length) {
          parts.push('临界：' + data.warnings.map(function (row) { return row.name + ' ' + row.saturation_after + '%'; }).join('、'));
        }
        if (data.recommendations && data.recommendations.length) {
          parts.push('推荐：' + data.recommendations.map(function (row) { return row.name + '（' + row.reason + '）'; }).join('；'));
        }
        toast(parts.length ? parts.join(' · ') : '试算完成：所选执行人均处于健康区间');
      }).catch(function (error) {
        toast(error.message, true);
      }).then(function () { recheck.disabled = false; });
    });
  }

  /* ---------------- AI 派工分析 ---------------- */
  var analyze = $('analyze-button');
  if (analyze) {
    analyze.addEventListener('click', function () {
      var picked = pickState();
      if (!picked.length) { toast('请先勾选执行人，再生成分析。', true); return; }
      analyze.disabled = true;
      analyze.textContent = '分析中…';
      post(DATA.urls.analyze, { task_id: TASK.task_id, assignees: picked }).then(function (body) {
        var data = body.data;
        var box = $('analyze-result');
        box.textContent = '';
        box.hidden = false;
        var head = document.createElement('div');
        head.className = 'ai-result-head';
        var label = document.createElement('span');
        label.className = 'detail-label';
        label.textContent = '关联度分析（仅供建议，不参与拦截）';
        var engine = document.createElement('span');
        engine.className = 'engine-chip';
        engine.textContent = '引擎：' + (data.engine === 'llm' ? 'AI 模型' : '规则兜底') + (data.degraded ? '（降级）' : '');
        head.appendChild(label);
        head.appendChild(engine);
        if (data.best) {
          var best = document.createElement('span');
          best.className = 'person-chip';
          best.textContent = '最匹配：' + data.best.name + '（关联度 ' + data.best.relation_score + '）';
          head.appendChild(best);
        }
        box.appendChild(head);
        (data.people || []).forEach(function (row) {
          var line = document.createElement('div');
          line.className = 'ai-person';
          var score = document.createElement('b');
          score.textContent = row.relation_score + ' 分';
          var name = document.createElement('strong');
          name.textContent = row.name;
          var note = document.createElement('span');
          note.textContent = row.note;
          line.appendChild(name);
          line.appendChild(score);
          line.appendChild(note);
          box.appendChild(line);
        });
        if ((data.risks || []).length) {
          var risks = document.createElement('div');
          risks.className = 'ai-risks';
          data.risks.forEach(function (text) {
            var chip = document.createElement('span');
            chip.className = 'risk-chip';
            chip.textContent = text;
            risks.appendChild(chip);
          });
          box.appendChild(risks);
        }
      }).catch(function (error) {
        toast(error.message, true);
      }).then(function () {
        analyze.disabled = false;
        analyze.textContent = 'AI 派工分析';
      });
    });
  }

  /* ---------------- 智能计划项 ---------------- */
  function planItemsFromDom() {
    var list = $('plan-draft-list');
    if (!list || !state.planDraft) { return null; }
    var rows = [];
    Array.prototype.forEach.call(list.querySelectorAll('.plan-draft-item'), function (row) {
      var hoursInput = row.querySelector('input.hours');
      var area = row.querySelector('textarea');
      rows.push({
        user_id: row.dataset.userId,
        plan_hours: Number(hoursInput.value || 0),
        start: row.dataset.start,
        end: row.dataset.end,
        acceptance: area.value.trim(),
        edited: row.classList.contains('is-edited')
      });
    });
    return rows;
  }

  function planSum() {
    var list = $('plan-draft-list');
    var sumEl = $('plan-sum');
    if (!list || !sumEl) { return; }
    var sum = 0;
    Array.prototype.forEach.call(list.querySelectorAll('input.hours'), function (input) {
      sum = round1(sum + Number(input.value || 0));
    });
    var taskHours = Number(TASK.plan_hours);
    sumEl.textContent = '计划项工时合计 ' + sum + 'h / 任务计划 ' + taskHours + 'h';
    sumEl.className = 'plan-sum' + (Math.abs(sum - taskHours) > 0.1 ? ' is-bad' : '');
  }

  var planButton = $('plan-button');
  if (planButton) {
    planButton.addEventListener('click', function () {
      var picked = pickState();
      if (!picked.length) { toast('请先勾选执行人，再生成计划项。', true); return; }
      planButton.disabled = true;
      planButton.textContent = '生成中…';
      post(DATA.urls.plan, { task_id: TASK.task_id, assignees: picked }).then(function (body) {
        state.planDraft = body.data.items;
        state.planSignature = signature(picked);
        var box = $('plan-draft');
        var list = $('plan-draft-list');
        var engine = $('plan-engine');
        box.hidden = false;
        if (engine) {
          engine.textContent = '引擎：' + (body.data.engine === 'llm' ? 'AI 模型' : '规则拆分') + (body.data.degraded ? '（时间窗由规则拆分）' : '');
        }
        list.textContent = '';
        body.data.items.forEach(function (item) {
          var li = document.createElement('li');
          li.className = 'plan-draft-item';
          li.dataset.userId = item.user_id;
          li.dataset.start = item.start;
          li.dataset.end = item.end;
          var who = document.createElement('span');
          who.className = 'who';
          who.textContent = item.name || item.user_id;
          var win = document.createElement('span');
          win.className = 'win';
          win.textContent = item.start + ' ~ ' + item.end;
          var hours = document.createElement('input');
          hours.type = 'number';
          hours.className = 'hours';
          hours.min = '0.5';
          hours.max = '120';
          hours.step = '0.5';
          hours.value = String(item.plan_hours);
          var mark = document.createElement('span');
          mark.className = 'edit-mark';
          mark.textContent = '人工调整';
          var area = document.createElement('textarea');
          area.maxLength = 200;
          area.value = item.acceptance || '';
          hours.addEventListener('input', function () {
            li.classList.add('is-edited');
            planSum();
          });
          area.addEventListener('input', function () { li.classList.add('is-edited'); });
          li.appendChild(who);
          li.appendChild(win);
          li.appendChild(hours);
          li.appendChild(mark);
          li.appendChild(area);
          list.appendChild(li);
        });
        planSum();
        toast('计划项草案已生成（' + body.data.items.length + ' 条），可编辑后下发');
      }).catch(function (error) {
        toast(error.message, true);
      }).then(function () {
        planButton.disabled = false;
        planButton.textContent = '生成计划项';
      });
    });
  }

  /* ---------------- 强制下发弹窗 ---------------- */
  function openOverrideModal(data, retry) {
    var overload = data.overload || [];
    var wrap = document.createElement('div');
    var impact = document.createElement('p');
    impact.className = 'modal-impact';
    impact.textContent = '以下执行人加入后超过 ' + TH.red + '% 上限：' +
      overload.map(function (row) { return row.name + '（' + row.saturation_after + '%）'; }).join('、');
    var canOverride = DATA.can_override;
    var checkLabel = null;
    var box = null;
    var area = null;
    var error = null;
    if (canOverride) {
      checkLabel = document.createElement('label');
      checkLabel.className = 'modal-field';
      checkLabel.style.display = 'flex';
      checkLabel.style.alignItems = 'center';
      checkLabel.style.gap = '10px';
      box = document.createElement('input');
      box.type = 'checkbox';
      box.style.width = '18px';
      box.style.height = '18px';
      box.style.accentColor = 'var(--accent)';
      var span = document.createElement('span');
      span.style.margin = '0';
      span.textContent = '强制下发（记录审计日志并在任务上标记超载）';
      checkLabel.appendChild(box);
      checkLabel.appendChild(span);
      var field = document.createElement('label');
      field.className = 'modal-field';
      var flabel = document.createElement('span');
      flabel.textContent = '强制下发理由（必填，至少 4 个字）';
      area = document.createElement('textarea');
      area.maxLength = 200;
      area.placeholder = '例：上线窗口临近，张伟是唯一熟悉该链路的人，后续通过日计划动态平衡负荷。';
      field.appendChild(flabel);
      field.appendChild(area);
      error = document.createElement('p');
      error.className = 'modal-error';
      error.textContent = '请勾选强制下发并填写理由（至少 4 个字）。';
      error.hidden = true;
      wrap.appendChild(checkLabel);
      wrap.appendChild(field);
      wrap.appendChild(error);
    } else {
      var tip = document.createElement('p');
      tip.className = 'modal-message';
      tip.textContent = '当前角色没有强制下发权限（action:assign.override），请更换执行人。';
      wrap.appendChild(tip);
    }
    var content = document.createElement('div');
    content.appendChild(impact);
    content.appendChild(wrap);

    window.NorthModal.open({
      title: canOverride ? '超载拦截 · 可强制下发' : '超载拦截',
      content: content,
      confirmText: canOverride ? '强制下发' : '知道了',
      danger: true,
      autoFocus: canOverride ? 'textarea' : null,
      onConfirm: function () {
        if (!canOverride) { return true; }
        if (!box.checked) {
          error.textContent = '请先勾选「强制下发」。';
          error.hidden = false;
          return false;
        }
        var reason = area.value.trim();
        if (reason.length < 4) {
          error.textContent = '请填写强制下发理由（至少 4 个字）。';
          error.hidden = false;
          return false;
        }
        return retry(true, reason).then(function () { return true; }).catch(function (err) {
          toast(err.message, true);
          return false;
        });
      }
    });
  }

  /* ---------------- 下发派工 ---------------- */
  function commitPayload(override, reason) {
    var picked = pickState();
    var payload = {
      task_id: TASK.task_id,
      assignees: picked,
      override: !!override,
      override_reason: reason || ''
    };
    var items = planItemsFromDom();
    if (items) { payload.plan_items = items; }
    return payload;
  }

  function doCommit(override, reason) {
    return post(DATA.urls.commit, commitPayload(override, reason));
  }

  var commitButton = $('commit-button');
  if (commitButton) {
    commitButton.addEventListener('click', function () {
      var picked = pickState();
      if (!picked.length) { return; }
      commitButton.disabled = true;
      commitButton.textContent = '下发中…';
      doCommit(false, '').then(function (body) {
        var data = body.data;
        var message = body.message;
        if ((data.warnings || []).length) {
          message += '；临界提醒：' + data.warnings.map(function (row) { return row.name + ' ' + row.saturation_after + '%'; }).join('、');
        }
        toast(message);
        setTimeout(function () {
          window.location.href = DATA.urls.assign + '?task_id=' + encodeURIComponent(TASK.task_id);
        }, 700);
      }).catch(function (error) {
        if (error.code === 1005 && error.data && error.data.overload) {
          openOverrideModal(error.data, function (flag, reasonText) {
            return doCommit(flag, reasonText).then(function (body) {
              toast(body.message);
              setTimeout(function () {
                window.location.href = DATA.urls.assign + '?task_id=' + encodeURIComponent(TASK.task_id);
              }, 700);
            });
          });
        } else {
          toast(error.message, true);
        }
      }).then(function () {
        commitButton.disabled = Math.abs(
          pickState().reduce(function (acc, item) { return acc + item.weight; }, 0) - 100) > 0.1;
        commitButton.textContent = '下发派工';
      });
    });
  }

  /* ---------------- 员工档案弹窗 ---------------- */
  function openProfile(userId) {
    get(DATA.urls.profile + '?user_id=' + encodeURIComponent(userId)).then(function (body) {
      var profile = body.data;
      var content = document.createElement('div');

      var metrics = document.createElement('div');
      metrics.className = 'profile-metrics';
      [['负荷', profile.load_hours + 'h'], ['基准容量', profile.base_capacity_hours + 'h'],
       ['饱和度', profile.saturation + '%'], ['在办任务', profile.open_titles.length + ' 项']].forEach(function (pair) {
        var box = document.createElement('div');
        var span = document.createElement('span');
        span.textContent = pair[0];
        var strong = document.createElement('strong');
        strong.textContent = pair[1];
        box.appendChild(span);
        box.appendChild(strong);
        metrics.appendChild(box);
      });
      content.appendChild(metrics);

      var tagsField = document.createElement('label');
      tagsField.className = 'modal-field';
      var tagsLabel = document.createElement('span');
      tagsLabel.textContent = '能力标签（逗号分隔，最多 8 个）';
      var tagsInput = document.createElement('input');
      tagsInput.type = 'text';
      tagsInput.value = (profile.ability_tags || []).join('，');
      tagsInput.disabled = !profile.editable;
      tagsField.appendChild(tagsLabel);
      tagsField.appendChild(tagsInput);
      content.appendChild(tagsField);

      var posField = document.createElement('label');
      posField.className = 'modal-field';
      var posLabel = document.createElement('span');
      posLabel.textContent = '岗位代码（如 backend / frontend / data）';
      var posInput = document.createElement('input');
      posInput.type = 'text';
      posInput.maxLength = 40;
      posInput.value = profile.position_code || '';
      posInput.disabled = !profile.editable;
      posField.appendChild(posLabel);
      posField.appendChild(posInput);
      content.appendChild(posField);

      var capField = document.createElement('label');
      capField.className = 'modal-field';
      var capLabel = document.createElement('span');
      capLabel.textContent = '基准容量（小时 / 周期）' + (profile.editable ? '' : ' · 需字段权限 field:staff_profile.capacity');
      var capInput = document.createElement('input');
      capInput.type = 'number';
      capInput.min = '1';
      capInput.max = '2000';
      capInput.step = '10';
      capInput.value = String(profile.base_capacity_hours);
      capInput.disabled = !profile.editable;
      capField.appendChild(capLabel);
      capField.appendChild(capInput);
      content.appendChild(capField);

      if ((profile.open_titles || []).length) {
        var open = document.createElement('p');
        open.className = 'modal-message';
        open.textContent = '在办任务：' + profile.open_titles.join('、');
        content.appendChild(open);
      }
      var error = document.createElement('p');
      error.className = 'modal-error';
      error.hidden = true;
      content.appendChild(error);

      window.NorthModal.open({
        title: profile.name + ' · 能力档案',
        content: content,
        confirmText: profile.editable ? '保存档案' : '关闭',
        onConfirm: function () {
          if (!profile.editable) { return true; }
          var capacity = Number(capInput.value || 0);
          if (capacity && (capacity < 1 || capacity > 2000)) {
            error.textContent = '基准容量须在 1~2000 小时之间。';
            error.hidden = false;
            return false;
          }
          return post(DATA.urls.save, {
            user_id: profile.user_id,
            position_code: posInput.value.trim(),
            ability_tags: tagsInput.value,
            base_capacity_hours: capacity
          }).then(function (body) {
            toast(body.message);
            setTimeout(function () { window.location.reload(); }, 700);
            return true;
          }).catch(function (err) {
            error.textContent = err.message;
            error.hidden = false;
            return false;
          });
        }
      });
    }).catch(function (error) { toast(error.message, true); });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-action="profile"]');
    if (!button) { return; }
    openProfile(button.dataset.id);
  });

  /* ---------------- 新建派工任务 ---------------- */
  function openCreateTask() {
    var content = document.createElement('div');
    var error = document.createElement('p');
    error.className = 'modal-error';
    error.hidden = true;

    var titleField = field('任务标题（必填，≤100 字）', 'input');
    titleField.input.maxLength = 100;
    var detailField = field('任务说明（可选，≤500 字）', 'textarea');
    detailField.input.maxLength = 500;

    var selectField = document.createElement('label');
    selectField.className = 'modal-field';
    var selectLabel = document.createElement('span');
    selectLabel.textContent = '挂接目标（仅已发布 / 执行中）';
    var select = document.createElement('select');
    (DATA.objective_rows || []).forEach(function (objective) {
      var option = document.createElement('option');
      option.value = objective.objective_id;
      option.textContent = objective.objective_id + ' · ' + objective.title + '（' + objective.org_name + '）';
      option.dataset.start = objective.plan_start || '';
      option.dataset.end = objective.plan_end || '';
      select.appendChild(option);
    });
    selectField.appendChild(selectLabel);
    selectField.appendChild(select);
    if (!(DATA.objective_rows || []).length) {
      selectLabel.textContent = '挂接目标（当前没有已发布 / 执行中的目标，请先在拆解树发布）';
      select.disabled = true;
    }

    var grid = document.createElement('div');
    grid.className = 'modal-grid-2';
    var priorityField = document.createElement('label');
    priorityField.className = 'modal-field';
    var priorityLabel = document.createElement('span');
    priorityLabel.textContent = '优先级';
    var prioritySelect = document.createElement('select');
    (DATA.priorities || ['高', '中', '低']).forEach(function (item) {
      var option = document.createElement('option');
      option.value = item;
      option.textContent = item;
      prioritySelect.appendChild(option);
    });
    priorityField.appendChild(priorityLabel);
    priorityField.appendChild(prioritySelect);

    var hoursField = field('计划工时（0.5~120h）', 'input');
    hoursField.input.type = 'number';
    hoursField.input.min = '0.5';
    hoursField.input.max = '120';
    hoursField.input.step = '0.5';
    hoursField.input.value = '8';
    grid.appendChild(priorityField);
    grid.appendChild(hoursField.label);

    var dateGrid = document.createElement('div');
    dateGrid.className = 'modal-grid-2';
    var startField = field('计划开始', 'input');
    startField.input.type = 'date';
    var endField = field('计划结束', 'input');
    endField.input.type = 'date';
    dateGrid.appendChild(startField.label);
    dateGrid.appendChild(endField.label);

    function applyObjectiveWindow() {
      var option = select.options[select.selectedIndex];
      if (!option) { return; }
      var start = option.dataset.start || '';
      var end = option.dataset.end || '';
      if (start) { startField.input.min = start; }
      if (end) { endField.input.max = end; }
      if (!startField.input.value) { startField.input.value = start; }
      if (!endField.input.value) { endField.input.value = end || start; }
    }
    select.addEventListener('change', applyObjectiveWindow);
    if ((DATA.objective_rows || []).length) { applyObjectiveWindow(); }

    content.appendChild(titleField.label);
    content.appendChild(selectField);
    content.appendChild(detailField.label);
    content.appendChild(grid);
    content.appendChild(dateGrid);
    content.appendChild(error);

    window.NorthModal.open({
      title: '新建派工任务',
      message: '任务挂接已发布目标后进入「待派工」，随即可在本页派给执行人。',
      content: content,
      confirmText: '创建任务',
      autoFocus: 'input',
      onConfirm: function () {
        var title = titleField.input.value.trim();
        if (!title) { error.textContent = '请填写任务标题。'; error.hidden = false; return false; }
        if (!(DATA.objective_rows || []).length) { error.textContent = '没有可挂接的目标。'; error.hidden = false; return false; }
        var hours = Number(hoursField.input.value || 0);
        if (!(hours >= 0.5 && hours <= 120)) { error.textContent = '计划工时须在 0.5~120 小时之间。'; error.hidden = false; return false; }
        if (!startField.input.value || !endField.input.value) { error.textContent = '请选择计划开始与结束日期。'; error.hidden = false; return false; }
        return post(DATA.urls.create, {
          title: title,
          detail: detailField.input.value.trim(),
          objective_id: select.value,
          priority: prioritySelect.value,
          plan_hours: hours,
          plan_start: startField.input.value,
          plan_end: endField.input.value
        }).then(function (body) {
          toast(body.message);
          window.location.href = body.data.redirect;
          return true;
        }).catch(function (err) {
          error.textContent = err.message;
          error.hidden = false;
          return false;
        });
      }
    });
  }

  function field(labelText, kind) {
    var label = document.createElement('label');
    label.className = 'modal-field';
    var span = document.createElement('span');
    span.textContent = labelText;
    var input = document.createElement(kind === 'textarea' ? 'textarea' : 'input');
    if (kind !== 'textarea') { input.type = 'text'; }
    label.appendChild(span);
    label.appendChild(input);
    return { label: label, input: input };
  }

  var createButton = $('create-task-button') || $('create-task-button-empty');
  if (createButton) { createButton.addEventListener('click', openCreateTask); }

  /* ---------------- 初始化 ---------------- */
  if (memberList && TASK) {
    var initial = {};
    (DATA.selected.assignees || []).forEach(function (item) { initial[item.user_id] = item.weight; });
    memberRows().forEach(function (row) {
      var input = row.querySelector('[data-role="weight"]');
      if (input && initial[row.dataset.userId] !== undefined) {
        input.value = String(initial[row.dataset.userId]);
      }
    });
    state.lastCount = pickState().length;
    if (memberList.querySelector('[data-action="profile"]')) { /* 档案入口已就绪 */ }
    syncAll();
  }
})();
