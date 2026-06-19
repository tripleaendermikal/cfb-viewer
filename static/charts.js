/* global Chart */
(function (global) {
  'use strict';

  const THEME = {
    text: '#8b9cb3',
    textBright: '#e7ecf3',
    grid: 'rgba(45, 58, 79, 0.55)',
    tooltipBg: '#1a2332',
    tooltipBorder: '#2d3a4f',
    fontFamily: '"Segoe UI", system-ui, sans-serif',
  };

  function hexToRgba(hex, alpha) {
    const h = String(hex || '#4a9eff').replace('#', '');
    if (h.length < 6) return `rgba(74, 158, 255, ${alpha})`;
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function solidColor(color) {
    return String(color || '#4a9eff').replace(/99$|66$/, '');
  }

  function initDefaults() {
    if (typeof Chart === 'undefined') return;
    Chart.defaults.color = THEME.text;
    Chart.defaults.borderColor = THEME.grid;
    Chart.defaults.font.family = THEME.fontFamily;
    Chart.defaults.plugins.legend.labels.color = THEME.textBright;
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.plugins.legend.labels.padding = 16;
    Chart.defaults.plugins.tooltip.backgroundColor = THEME.tooltipBg;
    Chart.defaults.plugins.tooltip.borderColor = THEME.tooltipBorder;
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.titleColor = THEME.textBright;
    Chart.defaults.plugins.tooltip.bodyColor = THEME.text;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.scale.grid.color = THEME.grid;
    Chart.defaults.scale.ticks.color = THEME.text;
  }

  function scaleTitle(text) {
    return { display: true, text, color: THEME.text, font: { size: 12 } };
  }

  function makeDistributionChart(canvas, labels, data, color, options) {
    initDefaults();
    const opts = options || {};
    const lineColor = solidColor(color);
    const plugins = [];
    if (opts.referenceIndex != null) {
      plugins.push({
        id: 'referenceLine',
        afterDraw(chart) {
          const idx = opts.referenceIndex;
          const x = chart.scales.x.getPixelForValue(idx);
          const { top, bottom, left, right } = chart.chartArea;
          if (x < left || x > right) return;
          const ctx = chart.ctx;
          ctx.save();
          ctx.strokeStyle = opts.referenceColor || '#f5a524';
          ctx.lineWidth = 2;
          ctx.setLineDash([5, 4]);
          ctx.beginPath();
          ctx.moveTo(x, top);
          ctx.lineTo(x, bottom);
          ctx.stroke();
          ctx.restore();
        },
      });
    }
    return new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: opts.label || 'Simulations',
          data,
          borderColor: lineColor,
          backgroundColor: hexToRgba(lineColor, 0.22),
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBackgroundColor: lineColor,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: opts.tooltipCallbacks || {},
          },
        },
        scales: {
          x: {
            title: opts.xTitle ? scaleTitle(opts.xTitle) : undefined,
            grid: { display: false },
            ticks: {
              maxRotation: opts.xRotation != null ? opts.xRotation : 0,
              minRotation: opts.xRotation != null ? opts.xRotation : 0,
              autoSkip: opts.xAutoSkip !== false,
              maxTicksLimit: opts.xMaxTicks || 12,
            },
          },
          y: {
            beginAtZero: true,
            title: opts.yTitle ? scaleTitle(opts.yTitle) : scaleTitle('Simulations'),
            suggestedMax: opts.ySuggestedMax,
          },
        },
      },
      plugins,
    });
  }

  function makeHistogramChart(canvas, labels, data, color, options) {
    initDefaults();
    const opts = options || {};
    const barColor = solidColor(color);
    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: opts.label || 'Simulations',
          data,
          backgroundColor: hexToRgba(barColor, 0.75),
          borderColor: barColor,
          borderWidth: 0,
          borderRadius: { topLeft: 4, topRight: 4 },
          borderSkipped: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            title: opts.xTitle ? scaleTitle(opts.xTitle) : undefined,
            grid: { display: false },
            ticks: {
              maxRotation: opts.xRotation != null ? opts.xRotation : 0,
              minRotation: opts.xRotation != null ? opts.xRotation : 0,
            },
          },
          y: {
            beginAtZero: true,
            title: opts.yTitle ? scaleTitle(opts.yTitle) : scaleTitle('Simulations'),
          },
        },
      },
    });
  }

  function makeCompareChart(canvas, datasets) {
    initDefaults();
    const lineDatasets = datasets.map(function (ds) {
      const c = solidColor(ds.borderColor || ds.backgroundColor);
      return {
        label: ds.label,
        data: ds.data,
        borderColor: c,
        backgroundColor: hexToRgba(c, 0.12),
        fill: false,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 2,
      };
    });
    return new Chart(canvas, {
      type: 'line',
      data: { labels: ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'], datasets: lineDatasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            align: 'start',
          },
        },
        scales: {
          x: { title: scaleTitle('Wins'), grid: { display: false } },
          y: { beginAtZero: true, title: scaleTitle('Simulations') },
        },
      },
    });
  }

  function makeHorizontalRank(canvas, items, color, options) {
    initDefaults();
    const opts = options || {};
    const barColor = solidColor(color);
    const sorted = items.slice().sort(function (a, b) { return b.value - a.value; });
    const labels = sorted.map(function (i) { return i.label; });
    const values = sorted.map(function (i) { return i.value; });
    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: hexToRgba(barColor, 0.8),
          borderColor: barColor,
          borderWidth: 0,
          borderRadius: 4,
          barThickness: opts.barThickness || 18,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                const suffix = opts.valueSuffix || '%';
                return ctx.parsed.x.toFixed(1) + suffix;
              },
            },
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            max: opts.max != null ? opts.max : 100,
            title: opts.xTitle ? scaleTitle(opts.xTitle) : scaleTitle('% of sims'),
            grid: { color: THEME.grid },
          },
          y: {
            grid: { display: false },
            ticks: {
              autoSkip: false,
              font: { size: 11 },
              callback: function (val) {
                const label = this.getLabelForValue(val);
                return label.length > 22 ? label.slice(0, 20) + '…' : label;
              },
            },
          },
        },
      },
    });
  }

  function rankChartHeight(itemCount, barThickness) {
    const bar = barThickness || 18;
    return Math.max(200, itemCount * (bar + 10) + 48);
  }

  function closestLabelIndex(labels, target) {
    if (target == null || !labels.length) return null;
    let best = Infinity;
    let idx = null;
    labels.forEach(function (label, i) {
      const v = parseFloat(label);
      if (Number.isNaN(v)) return;
      const d = Math.abs(v - target);
      if (d < best) {
        best = d;
        idx = i;
      }
    });
    return idx;
  }

  global.CFBCharts = {
    initDefaults,
    makeDistributionChart,
    makeHistogramChart,
    makeCompareChart,
    makeHorizontalRank,
    rankChartHeight,
    closestLabelIndex,
    hexToRgba,
  };
})(typeof window !== 'undefined' ? window : this);
