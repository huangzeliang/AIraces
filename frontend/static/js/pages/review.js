/* C 线 · 完成与验收页面的交互：AI 证据预审 + 差异说明联动 */
(function () {
  'use strict';

  var payload = {};
  var dataNode = document.getElementById('review-data');
  if (dataNode) {
    try { payload = JSON.parse(dataNode.textContent); } catch (error) { payload = {}; }
  }

  /* ---------------- AI 证据预审 ---------------- */
  var precheckBtn = document.getElementById('precheck-btn');
  if (precheckBtn) {
    precheckBtn.addEventListener('click', function () {
      var panel = document.getElementById('precheck-panel');
      var idle = document.getElementById('precheck-idle');
      var body = document.getElementById('precheck-body');
      var lines = document.getElementById('precheck-lines');
      var risksWrap = document.getElementById('precheck-risks-wrap');
      var risks = document.getElementById('precheck-risks');
      var advice = document.getElementById('precheck-advice');
      var source = document.getElementById('precheck-source');

      panel.hidden = false;
      idle.hidden = false;
      idle.textContent = '正在核对证据完备性…';
      body.hidden = true;
      precheckBtn.disabled = true;
      precheckBtn.textContent = '预审中…';

      fetch('/review/' + encodeURIComponent(payload.objective_id) + '/precheck', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          idle.hidden = true;
          body.hidden = false;
          lines.innerHTML = (data.lines || []).map(function (text) {
            return '<li>' + escapeHtml(text) + '</li>';
          }).join('') || '<li>暂无结论</li>';
          var riskList = data.risks || [];
          risksWrap.hidden = riskList.length === 0;
          risks.innerHTML = riskList.map(function (text) {
            return '<li>' + escapeHtml(text) + '</li>';
          }).join('');
          advice.textContent = data.advice || '';
          source.textContent = data.source === 'llm' ? '来源：大模型研判' : '来源：规则引擎';
          precheckBtn.disabled = false;
          precheckBtn.textContent = '重新预审';
          precheckBtn.setAttribute('aria-expanded', 'true');
        })
        .catch(function () {
          idle.textContent = '预审请求失败，请稍后重试。';
          precheckBtn.disabled = false;
          precheckBtn.textContent = 'AI 证据预审';
        });
    });
  }

  /* ---------------- 评分：差异说明联动 ---------------- */
  var finalScore = document.getElementById('final-score');
  var diffWrap = document.getElementById('diff-wrap');
  if (finalScore && diffWrap && payload.self_score !== null && payload.self_score !== undefined) {
    var sync = function () {
      var value = Number(finalScore.value);
      var need = finalScore.value !== '' && !isNaN(value) &&
        Math.abs(value - Number(payload.self_score)) > Number(payload.diff_limit || 20);
      diffWrap.hidden = !need;
    };
    finalScore.addEventListener('input', sync);
    sync();
  }

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
})();
