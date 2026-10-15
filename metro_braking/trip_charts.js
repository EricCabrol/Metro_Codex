/* Full-precision filtered signal review; no external plotting dependency. */
(() => {
  'use strict';
  const svgNode = (tag, attrs = {}, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  window.renderTripCharts = trips => {
    const root = document.getElementById('trip-charts');
    if (!root) return;
    root.replaceChildren();
    const contents = document.getElementById('run-contents');
    if (contents) contents.replaceChildren();
    const groups = new Map();
    if (!trips.length) {
      root.textContent = 'No validated interstation runs yet. Accept at least two stops per recording and export sections, or run analysis with --stops.';
      return;
    }
    const overview = document.createElement('p'); overview.className = 'duration-overview';
    const warnings = trips.filter(t => t.metrics.duration_warning).length;
    overview.textContent = `${warnings} running-duration warning(s) above 2 standard deviations. Only complete, station-assigned runs on the same line and in the same direction are compared; partial runs are excluded.`;
    root.append(overview);
    for (const [index, trip] of trips.entries()) {
      const m = trip.metrics, duration = m.end_s - m.start_s;
      const name = m.display_recording || m.recording;
      const runName = m.run_label || `Interstation run ${m.section}`;
      const origin = m.restart_s ?? m.start_s;
      const restartKnown = m.restart_s !== null;
      const card = document.createElement('details'); card.className = 'trip-signal'; card.id = `interstation-run-${index}`;
      const summary = document.createElement('summary');
      summary.textContent = `${name} / ${runName}${m.duration_warning ? " — Duration warning (>2 SD)" : ""}`;
      card.classList.toggle("has-duration-warning", !!m.duration_warning); summary.title = m.recording; card.append(summary);
      if (contents) {
        if (!groups.has(m.recording)) {
          const group = document.createElement('details');
          const title = document.createElement('summary'); title.textContent = name;
          const list = document.createElement('ul'); group.append(title, list); contents.append(group);
          groups.set(m.recording, list);
        }
        const item = document.createElement('li'), link = document.createElement('a');
        link.href = '#' + card.id; link.textContent = runName + (m.duration_warning ? " — Duration warning" : "");
        item.append(link); groups.get(m.recording).append(item);
      }
      const description = document.createElement('p'); description.className = 'trip-extrema';
      const format = (value, time) => value === null ? 'not measured' : `${value.toFixed(4)} m/s² at ${(time - m.start_s).toFixed(3)} s`;
      const windowLabel = restartKnown ? `Restart: ${m.restart_s.toFixed(3)} s in recording. Analysis: ${(m.window_start_s-origin).toFixed(3)}–${(m.window_end_s-origin).toFixed(3)} s after restart` : 'Departure not identified: extrema unavailable';
      description.textContent = `Maximum acceleration: ${format(m.max_acceleration_mps2, m.peak_time_s)}. Maximum deceleration: ${format(m.max_deceleration_mps2, m.deceleration_peak_time_s)}. ${windowLabel}; ${m.status}; ${m.deceleration_status}.`;
      if (m.coverage) {
        const assignment = document.createElement('p');
        assignment.textContent = `${m.station_assignment}. Coverage: ${m.coverage.replaceAll('_',' ')}.`;
        card.append(assignment);
        const timing = document.createElement('p'); timing.className = m.duration_warning ? 'duration-warning' : 'run-duration';
        const stats = m.duration_mean_s === null ? 'No comparable complete runs' : `Mean ${m.duration_mean_s.toFixed(2)} s; sample standard deviation ${m.duration_std_s === null ? 'unavailable' : m.duration_std_s.toFixed(2)+' s'}; n=${m.duration_sample_count}`;
        timing.textContent = `${m.duration_s === null ? 'Complete running duration unavailable' : 'Running duration: '+m.duration_s.toFixed(2)+' s'}. ${stats}. ${m.duration_warning ? 'Warning: running duration differs from the mean by more than 2 standard deviations.' : m.duration_check_status.replaceAll('_',' ')+'.'}`;
        card.append(timing);
      }
      card.append(description);
      const toolbar = document.createElement('div'); toolbar.className = 'trip-toolbar';
      const inputs = [];
      for (const label of ['From (s)', 'To (s)']) {
        const wrapper = document.createElement('label'); wrapper.textContent = label + ' ';
        const input = document.createElement('input'); input.type = 'number'; input.step = 'any'; input.min = 0; input.max = duration;
        input.setAttribute('aria-label', label); wrapper.append(input); toolbar.append(wrapper); inputs.push(input);
      }
      const button = (label, action) => {
        const b = document.createElement('button'); b.type = 'button'; b.textContent = label;
        b.onclick = action; toolbar.append(b);
      };
      const svg = svgNode('svg', {viewBox: '0 0 900 320', role: 'img', 'aria-label': 'Filtered acceleration and extrema', tabindex: '0'});
      svg.classList.add('trip-chart');
      const left = 65, right = 880, top = 25, bottom = 275;
      let lo = 0, hi = duration, anchor = null;
      const xTime = event => {
        const box = svg.getBoundingClientRect();
        const x = (event.clientX - box.left) / box.width * 900;
        return Math.max(lo, Math.min(hi, lo + (x - left) / (right - left) * (hi - lo)));
      };
      function view(start, end) {
        lo = Math.max(0, Math.min(duration, start)); hi = Math.max(0, Math.min(duration, end));
        if (hi - lo < Math.min(0.05, duration)) return;
        draw();
      }
      function zoom(factor, center = (lo + hi) / 2) {
        const span = Math.min(duration, Math.max(0.05, (hi - lo) * factor));
        const proportion = (center - lo) / (hi - lo);
        const start = Math.max(0, Math.min(duration - span, center - span * proportion));
        view(start, start + span);
      }
      function draw() {
        svg.replaceChildren(); svg.dataset.viewStart = lo; svg.dataset.viewEnd = hi;
        inputs[0].value = lo.toFixed(3); inputs[1].value = hi.toFixed(3);
        const x = t => left + (t - lo) / (hi - lo) * (right - left);
        const samples = trip.blocks.flatMap(block => block.filter(r => r[0] >= lo && r[0] <= hi));
        let ymin = 0, ymax = 0;
        for (const [, a] of samples) {ymin = Math.min(ymin, a); ymax = Math.max(ymax, a);}
        const pad = Math.max(0.1, (ymax - ymin) * 0.12); ymin -= pad; ymax += pad;
        const y = a => bottom - (a - ymin) / (ymax - ymin) * (bottom - top);
        svg.append(svgNode('rect', {x:left, y:top, width:right-left, height:bottom-top, fill:'#fff'}));
        const windowEnd = restartKnown ? Math.min(m.window_end_s - origin, duration, hi) : 0;
        const windowStart = restartKnown ? Math.max(lo, m.window_start_s - origin) : 0;
        if (windowEnd > windowStart) svg.append(svgNode('rect', {x:x(windowStart), y:top, width:x(windowEnd)-x(windowStart), height:bottom-top, fill:'#e9f2fa'}));
        for (let i = 0; i <= 5; i++) {
          const time = lo + (hi - lo) * i / 5;
          svg.append(svgNode('line', {x1:x(time), x2:x(time), y1:top, y2:bottom, stroke:'#d9e2ea'}));
          svg.append(svgNode('text', {x:x(time), y:295, 'text-anchor':'middle'}, time.toFixed(2)));
          const value = ymin + (ymax - ymin) * i / 5;
          svg.append(svgNode('text', {x:left-8, y:y(value)+4, 'text-anchor':'end'}, value.toFixed(2)));
        }
        svg.append(svgNode('line', {x1:left, x2:right, y1:y(0), y2:y(0), stroke:'#8493a1'}));
        for (const block of trip.blocks) {
          const points = block.filter(r => r[0] >= lo && r[0] <= hi)
            .map(r => `${x(r[0])},${y(r[1])}`).join(' ');
          svg.append(svgNode('polyline', {points, fill:'none', stroke:'#233e50', 'stroke-width':1.5}));
        }
        const marker = (time, value, color, name) => {
          if (time === null || value === null) return;
          const t = time - m.start_s;
          if (t < lo || t > hi) return;
          svg.append(svgNode('line', {x1:x(t), x2:x(t), y1:top, y2:bottom, stroke:color, 'stroke-dasharray':'5 4'}));
          const dot = svgNode('circle', {cx:x(t), cy:y(value), r:5, fill:color, class:'trip-marker', 'data-kind':name, 'data-time':t, 'data-value':value});
          dot.append(svgNode('title', {}, `${name}: ${value.toFixed(4)} m/s² at ${t.toFixed(3)} s`)); svg.append(dot);
        };
        marker(m.peak_time_s, m.max_acceleration_mps2, '#187bb6', 'acceleration');
        marker(m.deceleration_peak_time_s, m.max_deceleration_mps2 === null ? null : -m.max_deceleration_mps2, '#c65a19', 'deceleration');
        svg.append(svgNode('text', {x:left, y:16}, 'Filtered forward acceleration (m/s²)'));
        svg.append(svgNode('text', {x:(left+right)/2, y:316, 'text-anchor':'middle'}, restartKnown ? 'Seconds since restart time' : 'Seconds since available recording start (departure not identified)'));
      }
      inputs.forEach(input => input.onchange = () => {
        const from = Number(inputs[0].value), to = Number(inputs[1].value);
        if (inputs.some(i => i.value === '' || !i.checkValidity()) || to <= from) {draw(); return;}
        view(from, to);
      });
      button('Zoom in', () => zoom(0.5)); button('Zoom out', () => zoom(2));
      if (restartKnown) button(`Analysis window (${m.window_seconds} s)`, () => view(m.window_start_s-origin, Math.min(m.window_end_s-origin, duration)));
      button('Full interstation run', () => view(0, duration));
      svg.addEventListener('wheel', event => {event.preventDefault(); zoom(event.deltaY < 0 ? 0.8 : 1.25, xTime(event));}, {passive:false});
      svg.onpointerdown = event => {if(event.button !== 0)return; anchor = xTime(event); svg.setPointerCapture(event.pointerId);};
      svg.onpointerup = event => {
        if (anchor === null) return;
        const end = xTime(event), start = anchor; anchor = null;
        if (Math.abs(end-start) > 0.1) view(Math.min(start,end), Math.max(start,end));
      };
      svg.onpointercancel = () => {anchor = null;};
      svg.onkeydown = event => {
        if (event.key === '+' || event.key === '=') {event.preventDefault();zoom(0.5);}
        if (event.key === '-') {event.preventDefault();zoom(2);}
        if (event.key === 'Home') {event.preventDefault();view(0,duration);}
      };
      card.append(toolbar, svg); root.append(card); draw();
    }
    window.openReportAnchor?.();
  };
})();
