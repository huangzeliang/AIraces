/* NS 线图表渲染：进度环 / 关键路径依赖网络（SVG 字符串，颜色一律走 tokens.css 语义类）。 */
(function () {
  'use strict';

  function esc(text) {
    return String(text === undefined || text === null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function clamp(value) {
    var number = Number(value);
    if (isNaN(number)) { return 0; }
    return Math.max(0, Math.min(100, number));
  }

  /* ---------------- 进度环：SVG donut ---------------- */
  function ring(options) {
    options = options || {};
    var size = options.size || 116;
    var stroke = options.stroke || 11;
    var value = options.value === null || options.value === undefined ? null : clamp(options.value);
    var radius = (size - stroke) / 2;
    var circumference = 2 * Math.PI * radius;
    var ratio = value === null ? 0 : value / 100;
    var level = options.level || 'green';
    var dash = (circumference * ratio).toFixed(2) + ' ' + circumference.toFixed(2);
    var label = value === null ? '—' : (Math.round(value * 10) / 10) + '%';
    return '' +
      '<span class="ns-ring is-' + esc(level) + '" style="width:' + size + 'px;height:' + size + 'px">' +
      '<svg viewBox="0 0 ' + size + ' ' + size + '" role="img" aria-label="' + esc(options.aria || ('进度 ' + label)) + '">' +
      '<circle class="ns-ring-track" cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + radius + '" fill="none" stroke-width="' + stroke + '"></circle>' +
      '<circle class="ns-ring-value" cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + radius + '" fill="none" stroke-width="' + stroke + '"' +
      ' stroke-linecap="round" stroke-dasharray="' + dash + '" transform="rotate(-90 ' + size / 2 + ' ' + size / 2 + ')"></circle>' +
      '</svg>' +
      '<span class="ns-ring-label"><strong>' + label + '</strong>' + (options.caption ? '<small>' + esc(options.caption) + '</small>' : '') + '</span>' +
      '</span>';
  }

  /* ---------------- 关键路径依赖网络 ---------------- */
  function network(graph, options) {
    options = options || {};
    var nodes = (graph.nodes || []).slice();
    var edges = graph.edges || [];
    if (!nodes.length) {
      return '<div class="ns-empty">当前范围暂无任务节点，先在日计划与任务中创建任务。</div>';
    }
    var byId = {};
    nodes.forEach(function (node) { byId[node.task_id] = node; });
    var dayUnit = 14;          // 每工期天 ≈ 14px
    var nodeH = 46;
    var laneGap = 14;
    var marginX = 18;
    var marginY = 22;
    var minW = 96;

    var esList = nodes.map(function (n) { return Number(n.es) || 0; });
    var minEs = Math.min.apply(null, esList);
    var columns = {};
    nodes.forEach(function (node) {
      var key = String(Number(node.es) || 0);
      (columns[key] = columns[key] || []).push(node);
    });
    Object.keys(columns).forEach(function (key) {
      columns[key].sort(function (a, b) { return a.task_id < b.task_id ? -1 : 1; });
    });

    var box = {};
    var cursorY = marginY;
    Object.keys(columns).map(Number).sort(function (a, b) { return a - b; }).forEach(function (es) {
      var list = columns[String(es)];
      list.forEach(function (node) {
        var width = Math.max(minW, (Number(node.duration_days) || 1) * dayUnit + 46);
        box[node.task_id] = {
          x: marginX + (es - minEs) * dayUnit * 1.6 + 26,
          y: cursorY,
          w: width,
          h: nodeH,
          node: node,
        };
        cursorY += nodeH + laneGap;
      });
      cursorY = marginY;
    });
    // 同一 es 列内已按任务号排列；不同列错开则整体高度取决于最多的列
    var maxBottom = 0;
    var maxRight = 0;
    Object.keys(box).forEach(function (id) {
      var b = box[id];
      maxBottom = Math.max(maxBottom, b.y + b.h);
      maxRight = Math.max(maxRight, b.x + b.w);
    });

    var width = maxRight + marginX;
    var height = maxBottom + marginY;

    var parts = [];
    parts.push('<svg class="ns-net-svg" viewBox="0 0 ' + width + ' ' + height + '" width="' + width + '" height="' + height +
      '" role="img" aria-label="任务依赖与关键路径示意">');
    parts.push('<defs><marker id="ns-net-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">' +
      '<path d="M0,1 L7,4 L0,7 z" class="ns-net-arrowhead"></path></marker></defs>');

    edges.forEach(function (edge) {
      var a = box[edge.from_task_id];
      var b = box[edge.to_task_id];
      if (!a || !b) { return; }
      var level = (byId[edge.from_task_id] && byId[edge.from_task_id].is_critical &&
        byId[edge.to_task_id] && byId[edge.to_task_id].is_critical) ? ' is-critical' : '';
      var sx, sy, tx, ty;
      if (edge.dep_type === 'SS') { sx = a.x; sy = a.y + a.h / 2; tx = b.x; ty = b.y + a.h / 2; }
      else if (edge.dep_type === 'FF') { sx = a.x + a.w; sy = a.y + a.h / 2; tx = b.x + b.w; ty = b.y + b.h / 2; }
      else { sx = a.x + a.w; sy = a.y + a.h / 2; tx = b.x; ty = b.y + b.h / 2; }
      var midX = (sx + tx) / 2;
      var d = 'M' + sx + ',' + sy + ' C' + midX + ',' + sy + ' ' + midX + ',' + ty + ' ' + tx + ',' + ty;
      parts.push('<path class="ns-net-edge' + level + '" d="' + d + '" fill="none" marker-end="url(#ns-net-arrow)"></path>');
      var label = edge.dep_type + (edge.lag_days ? (edge.lag_days > 0 ? '+ ' : '') + edge.lag_days : '');
      parts.push('<text class="ns-net-edge-label" x="' + ((sx + tx) / 2) + '" y="' + ((sy + ty) / 2 - 5) + '">' + esc(label) + '</text>');
    });

    nodes.forEach(function (node) {
      var b = box[node.task_id];
      var cls = 'ns-net-node';
      if (node.is_critical) { cls += ' is-critical'; }
      if (node.is_hotspot) { cls += ' is-hotspot'; }
      if (node.status === 'blocked') { cls += ' is-blocked'; }
      var title = node.title.length > 9 ? node.title.slice(0, 9) + '…' : node.title;
      var rate = Math.round((Number(node.progress) || 0) * 10) / 10;
      parts.push('<g class="' + cls + '" data-task-id="' + esc(node.task_id) + '" tabindex="0" role="button" aria-label="' +
        esc(node.title + '，进度 ' + rate + '%，' + (node.is_critical ? '关键路径任务' : '非关键路径任务')) + '">');
      parts.push('<rect x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="' + b.h + '" rx="12"></rect>');
      parts.push('<text class="ns-net-title" x="' + (b.x + 10) + '" y="' + (b.y + 19) + '">' + esc(node.task_id + ' ' + title) + '</text>');
      parts.push('<text class="ns-net-sub" x="' + (b.x + 10) + '" y="' + (b.y + 36) + '">' + rate + '% · 工期 ' + node.duration_days +
        ' 天 · 时差 ' + node.total_float + ' 天</text>');
      if (node.is_hotspot) {
        parts.push('<circle class="ns-net-hot" cx="' + (b.x + b.w - 10) + '" cy="' + (b.y + 10) + '" r="5"></circle>');
      }
      parts.push('</g>');
    });
    parts.push('</svg>');
    return '<div class="ns-net-wrap">' + parts.join('') + '</div>';
  }

  window.NsCharts = { ring: ring, network: network, escapeHtml: esc };
})();
