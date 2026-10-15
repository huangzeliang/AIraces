/* AI 任务中枢（D）页面脚本：数字滚动、健康分圆环、严重度筛选、深度研判。
   零依赖实现；不使用任何外部图表库，符合《Northstar UI 主题开发规范》第 7 节。 */

(function () {
  'use strict';

  /* ---------------- 入场动效 ----------------
     数值（健康分、KPI）与圆环进度全部由服务端渲染成最终值，JS 不做任何改写。
     只给整页加一个类，由 CSS 负责淡入——这样在无 JS、无头渲染、切后台、
     动画帧被节流等任何情况下，页面上显示的都不可能是中间值。 */
  function reveal() {
    document.documentElement.classList.add('is-revealed');
  }

  /* ---------------- 卡点清单筛选 ---------------- */
  function bindFilter() {
    var group = document.querySelector('#blocker-filter');
    var list = document.querySelector('#blocker-list');
    if (!group || !list) return;
    var items = Array.prototype.slice.call(list.querySelectorAll('.blocker'));
    var empty = document.querySelector('#blocker-empty');

    group.addEventListener('click', function (event) {
      var chip = event.target.closest('.chip');
      if (!chip) return;
      var level = chip.getAttribute('data-level');
      group.querySelectorAll('.chip').forEach(function (node) {
        node.classList.toggle('is-active', node === chip);
      });
      var shown = 0;
      items.forEach(function (item) {
        var hit = level === 'all' || item.getAttribute('data-level') === level;
        item.classList.toggle('is-hidden', !hit);
        if (hit) shown += 1;
      });
      if (empty) empty.hidden = shown !== 0;
    });
  }

  /* ---------------- 重新研判 ---------------- */
  function bindRefresh() {
    var button = document.querySelector('#hub-refresh');
    if (!button) return;
    button.addEventListener('click', function () {
      button.disabled = true;
      button.textContent = '研判中…';
      window.location.reload();
    });
  }

  /* ---------------- 深度研判（大模型，可回退规则） ---------------- */
  function bindDeep() {
    var button = document.querySelector('#deep-btn');
    if (!button) return;
    var body = document.querySelector('#deep-body');
    var idle = document.querySelector('#deep-idle');
    var list = document.querySelector('#deep-lines');
    var advice = document.querySelector('#deep-advice');
    var source = document.querySelector('#deep-source');

    button.addEventListener('click', function () {
      button.disabled = true;
      button.textContent = '研判中…';
      fetch('/hub/deep', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
        .then(function (response) { return response.json(); })
        .then(function (result) {
          list.innerHTML = '';
          (result.lines || []).forEach(function (line) {
            var li = document.createElement('li');
            li.textContent = line;
            list.appendChild(li);
          });
          advice.textContent = result.advice || '';
          advice.hidden = !result.advice;
          source.textContent = result.source === 'llm'
            ? '来源：大模型研判'
            : '未配置大模型，以上为规则引擎结论（在系统设置里配置模型后可切换为深度研判）。';
          if (idle) idle.hidden = true;
          body.hidden = false;
          button.textContent = '重新研判';
        })
        .catch(function () {
          source.textContent = '研判请求失败，请稍后重试。';
          if (idle) idle.hidden = true;
          body.hidden = false;
          button.textContent = '开始研判';
        })
        .then(function () { button.disabled = false; });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    reveal();
    bindFilter();
    bindRefresh();
    bindDeep();
  });
})();
