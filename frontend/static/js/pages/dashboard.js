/* 驾驶舱（C1）页面脚本：数字滚动、原生 SVG 折线/柱/环图、拆解树、周报与助手。
   零依赖实现；所有图表均为手写 SVG，符合《Northstar UI 主题开发规范》第 7 节「禁止引入外部图表库」。 */

(function () {
  'use strict';

  var motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  var prefersReduced = motionQuery.matches;

  var payload = document.querySelector('#dashboard-data');
  var data = payload ? JSON.parse(payload.textContent) : null;

  var SVG_NS = 'http://www.w3.org/2000/svg';

  function el(name, attrs) {
    var node = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    return node;
  }

  function text(content, attrs) {
    var node = el('text', attrs || {});
    node.textContent = content;
    return node;
  }

  /* ---------------- 数字滚动 ---------------- */
  function animateNumbers() {
    document.querySelectorAll('[data-count]').forEach(function (node) {
      var target = Number(node.getAttribute('data-count')) || 0;
      var suffix = node.getAttribute('data-suffix') || '';
      if (prefersReduced || target === 0) {
        node.textContent = target + suffix;
        return;
      }
      var duration = 900;
      var started = null;
      function step(now) {
        if (started === null) started = now;
        var progress = Math.min((now - started) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        node.textContent = Math.round(target * eased) + suffix;
        if (progress < 1) window.requestAnimationFrame(step);
      }
      window.requestAnimationFrame(step);
    });
  }

  /* ---------------- 北极星圆环 ---------------- */
  function animateRing() {
    var circle = document.querySelector('.ring-value');
    if (!circle) return;
    var circumference = 2 * Math.PI * 52;
    var value = Math.min(Number(circle.getAttribute('data-ring')) || 0, 100);
    circle.style.strokeDasharray = String(circumference);
    circle.style.strokeDashoffset = String(circumference);
    window.requestAnimationFrame(function () {
      circle.style.strokeDashoffset = String(circumference * (1 - value / 100));
    });
  }

  /* ---------------- 折线图 ---------------- */
  function renderTrend(container, points) {
    if (!container) return;
    if (!points || !points.length) {
      container.innerHTML = '<p class="chart-empty">该周期暂无数据</p>';
      return;
    }
    var W = 760;
    var H = 240;
    var pad = { top: 20, right: 18, bottom: 34, left: 36 };
    var innerW = W - pad.left - pad.right;
    var innerH = H - pad.top - pad.bottom;
    var maxValue = Math.max(1, points.reduce(function (max, p) {
      return Math.max(max, p.created, p.completed);
    }, 0));
    var ticks = 4;
    var stepX = points.length > 1 ? innerW / (points.length - 1) : 0;

    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'chart-svg' });

    for (var i = 0; i <= ticks; i += 1) {
      var gridY = pad.top + (innerH / ticks) * i;
      svg.appendChild(el('line', { x1: pad.left, y1: gridY, x2: W - pad.right, y2: gridY, class: 'grid-line' }));
      svg.appendChild(text(String(Math.round(maxValue * (1 - i / ticks))), {
        x: 8, y: gridY + 4, class: 'axis-text'
      }));
    }

    function pointFor(index, value) {
      return {
        x: pad.left + stepX * index,
        y: pad.top + innerH - (value / maxValue) * innerH
      };
    }

    function linePath(key) {
      return points.map(function (p, index) {
        var pt = pointFor(index, p[key]);
        return (index === 0 ? 'M' : 'L') + pt.x.toFixed(1) + ' ' + pt.y.toFixed(1);
      }).join(' ');
    }

    ['created', 'completed'].forEach(function (key) {
      svg.appendChild(el('path', { d: linePath(key), class: 'series-' + key }));
    });

    var labelStep = Math.max(1, Math.ceil(points.length / 10));
    points.forEach(function (p, index) {
      ['created', 'completed'].forEach(function (key) {
        var pt = pointFor(index, p[key]);
        var dot = el('circle', { cx: pt.x, cy: pt.y, r: 3.4, class: 'point point-' + key });
        var title = el('title');
        title.textContent = p.label + ' · ' + (key === 'created' ? '新增' : '完成') + ' ' + p[key];
        dot.appendChild(title);
        svg.appendChild(dot);
      });
      if (index % labelStep === 0) {
        svg.appendChild(text(p.label, {
          x: pointFor(index, 0).x, y: H - 10, class: 'axis-text', 'text-anchor': 'middle'
        }));
      }
    });

    container.innerHTML = '';
    container.appendChild(svg);
  }

  /* ---------------- 柱状图 ---------------- */
  function renderBars(container, list) {
    if (!container) return;
    if (!list || !list.length) {
      container.innerHTML = '<p class="chart-empty">暂无数据</p>';
      return;
    }
    var W = 360;
    var H = 240;
    var pad = { top: 26, right: 16, bottom: 34, left: 16 };
    var innerW = W - pad.left - pad.right;
    var innerH = H - pad.top - pad.bottom;
    var maxValue = Math.max(1, list.reduce(function (max, item) { return Math.max(max, item.count); }, 0));
    var slot = innerW / list.length;
    var barW = Math.min(58, slot * 0.52);
    var palette = ['var(--prio-high)', 'var(--prio-mid)', 'var(--prio-low)',
      'var(--green)', 'var(--purple)', 'var(--accent)'];

    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'chart-svg' });
    svg.appendChild(el('line', {
      x1: pad.left, y1: H - pad.bottom, x2: W - pad.right, y2: H - pad.bottom, class: 'grid-line'
    }));

    list.forEach(function (item, index) {
      var height = Math.max(2, (item.count / maxValue) * innerH);
      var x = pad.left + slot * index + (slot - barW) / 2;
      var y = pad.top + innerH - height;
      var bar = el('rect', {
        x: x, y: y, width: barW, height: height, rx: 8,
        class: 'bar', fill: palette[index % palette.length]
      });
      var title = el('title');
      title.textContent = item.dimension + '：' + item.count + ' 项';
      bar.appendChild(title);
      svg.appendChild(bar);
      svg.appendChild(text(String(item.count), {
        x: x + barW / 2, y: y - 8, class: 'bar-value', 'text-anchor': 'middle'
      }));
      svg.appendChild(text(item.dimension, {
        x: x + barW / 2, y: H - 12, class: 'bar-label', 'text-anchor': 'middle'
      }));
    });

    container.innerHTML = '';
    container.appendChild(svg);
  }

  /* ---------------- 环形图 ---------------- */
  function renderDonut(container, list) {
    if (!container) return;
    var total = (list || []).reduce(function (sum, item) { return sum + item.count; }, 0);
    if (!total) {
      container.innerHTML = '<p class="chart-empty">该周期暂无数据</p>';
      return;
    }
    var size = 240;
    var center = size / 2;
    var outer = 88;
    var inner = 58;
    var colors = ['var(--accent)', 'var(--green)', 'var(--purple)', 'var(--prio-mid)',
      'var(--prio-high)', 'var(--prio-low)'];
    var angle = -Math.PI / 2;

    function arcPath(startAngle, endAngle, outerR, innerR) {
      var x1 = center + outerR * Math.cos(startAngle);
      var y1 = center + outerR * Math.sin(startAngle);
      var x2 = center + outerR * Math.cos(endAngle);
      var y2 = center + outerR * Math.sin(endAngle);
      var x3 = center + innerR * Math.cos(endAngle);
      var y3 = center + innerR * Math.sin(endAngle);
      var x4 = center + innerR * Math.cos(startAngle);
      var y4 = center + innerR * Math.sin(startAngle);
      var largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
      return 'M' + x1 + ' ' + y1 +
        ' A' + outerR + ' ' + outerR + ' 0 ' + largeArc + ' 1 ' + x2 + ' ' + y2 +
        ' L' + x3 + ' ' + y3 +
        ' A' + innerR + ' ' + innerR + ' 0 ' + largeArc + ' 0 ' + x4 + ' ' + y4 + ' Z';
    }

    var svg = el('svg', { viewBox: '0 0 ' + size + ' ' + size, class: 'chart-svg' });
    var legend = document.createElement('div');
    legend.className = 'donut-legend';

    list.forEach(function (item, index) {
      if (!item.count) return;
      var sweep = (item.count / total) * Math.PI * 2;
      var color = colors[index % colors.length];
      var seg = el('path', { d: arcPath(angle, angle + sweep, outer, inner), class: 'donut-seg', fill: color });
      var title = el('title');
      title.textContent = item.dimension + '：' + item.count + ' 项（' + Math.round(item.count / total * 100) + '%）';
      seg.appendChild(title);
      svg.appendChild(seg);
      angle += sweep;

      var chip = document.createElement('span');
      var swatch = document.createElement('i');
      swatch.style.background = color;
      chip.appendChild(swatch);
      chip.appendChild(document.createTextNode(item.dimension + ' ' + item.count));
      legend.appendChild(chip);
    });

    svg.appendChild(text(String(total), { x: center, y: center + 2, class: 'donut-value', 'text-anchor': 'middle' }));
    svg.appendChild(text('任务总数', { x: center, y: center + 20, class: 'donut-caption', 'text-anchor': 'middle' }));

    container.innerHTML = '';
    container.appendChild(svg);
    container.appendChild(legend);
  }

  /* ---------------- 北极星拆解树 ---------------- */
  function renderTree(container, northStar) {
    if (!container || !northStar) return;
    var subs = northStar.sub_metrics || [];
    if (!subs.length) {
      container.innerHTML = '<p class="chart-empty">暂无指标定义</p>';
      return;
    }
    var W = 900;
    var H = 340;
    var rootY = 46;
    var subY = 168;
    var leafY = 288;
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H });

    function node(x, y, width, height, title, value, variant) {
      var g = el('g', { class: 'tree-group ' + (variant || '') });
      g.appendChild(el('rect', {
        x: x - width / 2, y: y - height / 2, width: width, height: height, rx: 14, class: 'tree-node'
      }));
      g.appendChild(text(title, { x: x, y: y - 4, class: 'tree-title', 'text-anchor': 'middle' }));
      g.appendChild(text(value, { x: x, y: y + 14, class: 'tree-value', 'text-anchor': 'middle' }));
      svg.appendChild(g);
      return g;
    }

    node(W / 2, rootY, 230, 56, northStar.north_star_metric, northStar.value + northStar.unit, 'tree-root');

    var slot = W / subs.length;
    subs.forEach(function (sub, index) {
      var cx = slot * index + slot / 2;
      svg.appendChild(el('path', {
        d: 'M' + (W / 2) + ' ' + (rootY + 28) +
          ' C' + (W / 2) + ' ' + (subY - 40) + ' ' + cx + ' ' + (subY - 40) + ' ' + cx + ' ' + (subY - 26),
        class: 'tree-link'
      }));
      node(cx, subY, 176, 52, sub.name, sub.value + sub.unit);

      var children = sub.children || [];
      children.forEach(function (child, childIndex) {
        var offset = children.length > 1 ? (childIndex === 0 ? -52 : 52) : 0;
        var lx = cx + offset;
        svg.appendChild(el('path', {
          d: 'M' + cx + ' ' + (subY + 26) +
            ' C' + cx + ' ' + (leafY - 30) + ' ' + lx + ' ' + (leafY - 30) + ' ' + lx + ' ' + (leafY - 18),
          class: 'tree-link'
        }));
        var g = el('g', { class: 'tree-group' });
        g.appendChild(el('rect', {
          x: lx - 46, y: leafY - 18, width: 92, height: 36, rx: 11, class: 'tree-node'
        }));
        g.appendChild(text(child.name, { x: lx, y: leafY - 3, class: 'tree-leaf-label', 'text-anchor': 'middle' }));
        g.appendChild(text(child.value + child.unit, { x: lx, y: leafY + 12, class: 'tree-leaf', 'text-anchor': 'middle' }));
        svg.appendChild(g);
      });
    });

    container.innerHTML = '';
    container.appendChild(svg);
  }

  /* ---------------- AI 周报 ---------------- */
  function setupReport() {
    var toggle = document.querySelector('#report-toggle');
    var body = document.querySelector('#report-body');
    if (!toggle || !body) return;
    toggle.addEventListener('click', function () {
      var expanded = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', String(!expanded));
      body.hidden = expanded;
      toggle.textContent = expanded ? '生成周报' : '收起周报';
    });
  }

  /* ---------------- 智能助手（演示级预设问答） ---------------- */
  function setupBot() {
    var form = document.querySelector('#bot-form');
    var input = document.querySelector('#bot-text');
    var windowNode = document.querySelector('#bot-window');
    if (!form || !input || !windowNode) return;

    var presets = [
      { keys: ['今天', '该做', '优先'], reply: '先清掉「今日到期」的任务，再跟进延期次数最高的那一项。' },
      { keys: ['延期', '风险', '逾期'], reply: '延期最集中的任务已在「最常延期任务」榜里列出，建议复盘它们的时间估算口径。' },
      { keys: ['拆解', '拆', '子任务'], reply: '把预计耗时最长的那项任务拆成 2-3 个子任务，能把风险前置到更早的节点。' },
      { keys: ['完成率', '指标', '健康'], reply: '完成率取自「周期内已完成 ÷ 周期内总数」，可在顶部切换今日 / 本周 / 本月分别查看。' }
    ];

    function bubble(role, content) {
      var line = document.createElement('p');
      line.className = 'bot-line ' + (role === 'user' ? 'from-user' : 'from-bot');
      line.textContent = content;
      windowNode.appendChild(line);
      windowNode.scrollTop = windowNode.scrollHeight;
    }

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var value = input.value.trim();
      if (!value) return;
      bubble('user', value);
      input.value = '';
      var matched = presets.find(function (item) {
        return item.keys.some(function (key) { return value.indexOf(key) !== -1; });
      });
      bubble('bot', matched
        ? matched.reply
        : '这个问题暂时超出了预设范围。可以试试问：「今天该做什么」「哪些任务有延期风险」「完成率怎么算」。');
    });
  }

  /* ---------------- 初始化 ---------------- */
  animateNumbers();
  animateRing();
  if (data) {
    renderTrend(document.querySelector('#trend-chart'), data.trend);
    renderBars(document.querySelector('#priority-chart'), data.priority);
    renderDonut(document.querySelector('#status-chart'), data.status);
    renderTree(document.querySelector('#metric-tree'), data.north_star);
  }
  setupReport();
  setupBot();

  motionQuery.addEventListener('change', function (event) {
    prefersReduced = event.matches;
  });
})();
