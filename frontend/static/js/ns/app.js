/* NS 线公共前端：请求包装（错误码统一处理 + toast）、格式化工具。 */
(function () {
  'use strict';

  function toastRegion() {
    var region = document.getElementById('ns-toast-region');
    if (!region) {
      region = document.createElement('div');
      region.className = 'ns-toast-region';
      region.id = 'ns-toast-region';
      region.setAttribute('aria-live', 'polite');
      document.body.appendChild(region);
    }
    return region;
  }

  function toast(message, type) {
    var region = toastRegion();
    var item = document.createElement('div');
    item.className = 'ns-toast' + (type ? ' is-' + type : '');
    item.setAttribute('role', type === 'error' ? 'alert' : 'status');
    item.textContent = message;
    region.appendChild(item);
    setTimeout(function () { item.remove(); }, type === 'error' ? 5200 : 3600);
  }

  function request(method, url, body, options) {
    options = options || {};
    var headers = {};
    var init = { method: method, headers: headers, credentials: 'same-origin' };
    if (options.idempotencyKey) {
      headers['Idempotency-Key'] = options.idempotencyKey;
    } else if (method !== 'GET') {
      headers['Idempotency-Key'] = 'ns-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
    }
    if (body instanceof FormData) {
      init.body = body;
    } else if (body !== undefined && body !== null) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    return fetch(url, init).then(function (response) {
      return response.json().catch(function () { return { code: 1006, message: '响应解析失败' }; })
        .then(function (payload) {
          if (payload.code !== 0) {
            var error = new Error(payload.message || '请求失败');
            error.code = payload.code;
            error.data = payload.data;
            if (!options.silent) { toast(error.message, 'error'); }
            throw error;
          }
          return payload.data;
        });
    });
  }

  var NsApi = {
    get: function (url, options) { return request('GET', url, null, options); },
    post: function (url, body, options) { return request('POST', url, body || {}, options); },
    postForm: function (url, formData, options) { return request('POST', url, formData, options); }
  };

  function escapeHtml(text) {
    return String(text === undefined || text === null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtPct(value) {
    if (value === null || value === undefined || value === '') { return '—'; }
    var number = Number(value);
    return (Math.round(number * 10) / 10) + '%';
  }

  function fmtDate(value) {
    if (!value) { return '—'; }
    return String(value).slice(0, 10);
  }

  function fmtDateTime(value) {
    if (!value) { return '—'; }
    return String(value).slice(0, 16).replace('T', ' ');
  }

  var LEVEL_TEXT = { green: '正常', yellow: '预警', red: '严重' };
  var HEALTH_TEXT = { ahead: '超前', normal: '正常', lagging: '滞后' };

  function levelPill(level) {
    return '<span class="ns-pill is-' + (level === 'green' ? 'green' : level === 'yellow' ? 'yellow' : 'red') + '">' +
      (LEVEL_TEXT[level] || level) + '</span>';
  }

  function healthPill(health) {
    var cls = health === 'ahead' ? 'green' : health === 'normal' ? 'green' : 'red';
    return '<span class="ns-pill is-' + cls + '">' + (HEALTH_TEXT[health] || health) + '</span>';
  }

  function bar(value, cls) {
    var width = Math.max(0, Math.min(100, Number(value) || 0));
    return '<span class="ns-bar ' + (cls || '') + '" role="img" aria-label="进度 ' + fmtPct(value) + '"><i style="width:' + width + '%"></i></span>';
  }

  window.Ns = {
    api: NsApi, toast: toast, escapeHtml: escapeHtml,
    fmtPct: fmtPct, fmtDate: fmtDate, fmtDateTime: fmtDateTime,
    levelPill: levelPill, healthPill: healthPill, bar: bar,
    LEVEL_TEXT: LEVEL_TEXT, HEALTH_TEXT: HEALTH_TEXT
  };
})();
