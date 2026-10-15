/* Compare validated runs on the same line and directed station pair. */
(() => {
  'use strict';
  const colors = ['#0072b2', '#d55e00', '#009e73', '#a23b96', '#8c6500', '#4b4bba', '#007e87', '#b43842'];
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const svgNode = (tag, attrs, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  window.renderRunComparisons = (trips, {linkRuns = true} = {}) => {
    const root = document.getElementById('run-comparison-charts');
    if (!root) return;
    root.replaceChildren();
    const groups = new Map();
    for (const [index, trip] of trips.entries()) {
      const m = trip.metrics;
      if (!m.duration_eligible || m.coverage !== 'complete' || !Number.isFinite(m.restart_s) ||
          !m.line || !m.origin_station || !m.destination_station) continue;
      const key = JSON.stringify([m.line, m.origin_station, m.destination_station]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({trip, index});
    }
    const comparable = [...groups.entries()].filter(([, entries]) => entries.length >= 2)
      .sort((a,b) => a[0].localeCompare(b[0]));
    if (!comparable.length) {
      root.textContent = 'No repeated complete, station-assigned runs yet. Review stops and export sections to compare the same interstation on the same line and in the same direction.';
      return;
    }
    const chooser = element('label', 'Interstation '), select = element('select');
    select.setAttribute('aria-label', 'Interstation to compare');
    for (const [key, entries] of comparable) {
      const m = entries[0].trip.metrics, option = element('option', `L${m.line} ${m.origin_station} – ${m.destination_station} (${entries.length} runs)`);
      option.value = key; select.append(option);
    }
    chooser.append(select); root.append(chooser);
    const panel = element('div', undefined, 'run-comparison-panel');root.append(panel);
    function show() {
      panel.replaceChildren();
      const entries = groups.get(select.value), m = entries[0].trip.metrics;
      const heading = element('h3', `L${m.line} ${m.origin_station} – ${m.destination_station}`);panel.append(heading);
      panel.append(element('p', `${entries.length} complete runs. Filtered forward acceleration aligned at validated restart (t = 0). Station names inferred from stop order.`, 'comparison-description'));
      const traces = entries.map(({trip, index}, i) => ({trip, index, color: colors[i % colors.length],
        dash: i >= colors.length ? '7 4' : '', visible:true,
        blocks:trip.blocks.map(block => block.map(([t,a]) => [t + (trip.metrics.start_s - trip.metrics.restart_s), a]))}));
      const duration = Math.max(...traces.map(t => t.trip.metrics.end_s - t.trip.metrics.restart_s));
      const toolbar = element('div', undefined, 'trip-toolbar');panel.append(toolbar);
      const inputs = [];
      for (const label of ['From (s)', 'To (s)']) {
        const wrapper = element('label', label+' '), input = element('input');
        input.type='number';input.step='any';input.min=0;input.max=duration;input.setAttribute('aria-label',label);
        wrapper.append(input);toolbar.append(wrapper);inputs.push(input);
      }
      const button = (text, action) => {const b=element('button',text);b.type='button';b.onclick=action;toolbar.append(b);};
      const svg = svgNode('svg', {viewBox:'0 0 1100 400', role:'img', tabindex:'0', 'aria-label':`Overlaid filtered acceleration: ${heading.textContent}`});
      svg.classList.add('run-comparison-chart');panel.append(svg);
      const legend = element('div', undefined, 'comparison-legend');panel.append(legend);
      for (const trace of traces) {
        const row = element('div', undefined, 'comparison-legend-row'), label = element('label'), check = element('input');
        check.type='checkbox';check.checked=true;
        check.setAttribute('aria-label',`Show ${trace.trip.metrics.display_recording || trace.trip.metrics.recording}`);
        check.onchange=()=>{trace.visible=check.checked;draw();};
        const swatch=element('span',undefined,'comparison-swatch');swatch.style.borderColor=trace.color;
        swatch.style.borderTopStyle=trace.dash?'dashed':'solid';label.append(check,swatch);row.append(label);
        const link=element(linkRuns?'a':'span',trace.trip.metrics.display_recording || trace.trip.metrics.recording);
        if(linkRuns)link.href=`#interstation-run-${trace.index}`;
        row.append(link,element('span',` — ${(trace.trip.metrics.end_s-trace.trip.metrics.restart_s).toFixed(2)} s`));legend.append(row);
      }
      const left=70, right=1080, top=30, bottom=345;
      let lo=0, hi=duration, anchor=null;
      function view(from,to) {
        const start=Math.max(0,Math.min(duration,from)), end=Math.max(0,Math.min(duration,to));
        if (!Number.isFinite(start) || !Number.isFinite(end) || end-start<Math.min(.05,duration)) return;
        lo=start;hi=end;draw();
      }
      function zoom(factor, center=(lo+hi)/2) {
        const span=Math.min(duration,Math.max(.05,(hi-lo)*factor));
        const start=Math.max(0,Math.min(duration-span,center-span*(center-lo)/(hi-lo)));
        view(start,start+span);
      }
      function draw() {
        svg.replaceChildren();svg.dataset.viewStart=lo;svg.dataset.viewEnd=hi;
        inputs[0].value=lo.toFixed(3);inputs[1].value=hi.toFixed(3);
        const visible=traces.filter(t=>t.visible), samples=visible.flatMap(t=>t.blocks.flatMap(b=>b.filter(([t])=>t>=lo && t<=hi)));
        let ymin=0,ymax=0;
        for(const [,a] of samples){ymin=Math.min(ymin,a);ymax=Math.max(ymax,a);}
        const pad=Math.max(.1,(ymax-ymin)*.1);ymin-=pad;ymax+=pad;
        const x=t=>left+(t-lo)/(hi-lo)*(right-left), y=a=>bottom-(a-ymin)/(ymax-ymin)*(bottom-top);
        svg.append(svgNode('rect',{x:left,y:top,width:right-left,height:bottom-top,fill:'#fff'}));
        const defs=svgNode('defs',{}), clip=svgNode('clipPath',{id:'run-comparison-clip'});
        clip.append(svgNode('rect',{x:left,y:top,width:right-left,height:bottom-top}));defs.append(clip);svg.append(defs);
        const curves=svgNode('g',{'clip-path':'url(#run-comparison-clip)'});
        for(let i=0;i<=5;i++) {
          const time=lo+(hi-lo)*i/5, value=ymin+(ymax-ymin)*i/5;
          svg.append(svgNode('line',{x1:x(time),x2:x(time),y1:top,y2:bottom,stroke:'#dce4eb'}));
          svg.append(svgNode('line',{x1:left,x2:right,y1:y(value),y2:y(value),stroke:'#e9eef2'}));
          svg.append(svgNode('text',{x:x(time),y:368,'text-anchor':'middle'},time.toFixed(1)));
          svg.append(svgNode('text',{x:left-10,y:y(value)+4,'text-anchor':'end'},value.toFixed(2)));
        }
        svg.append(svgNode('line',{x1:left,x2:right,y1:y(0),y2:y(0),stroke:'#8493a1'}));
        for(const trace of visible) {
          for(const block of trace.blocks) {
            // Keep neighbouring samples so the curve reaches the zoom boundaries.
            const first=block.findIndex(([t])=>t>=lo);
            if(first<0)continue;
            let last=block.findIndex(([t])=>t>hi);if(last<0)last=block.length;
            const points=block.slice(Math.max(0,first-1),Math.min(block.length,last+1));
            const curve=svgNode('polyline',{points:points.map(([t,a])=>`${x(t)},${y(a)}`).join(' '),fill:'none',
              stroke:trace.color,'stroke-width':1.8,'stroke-dasharray':trace.dash,'data-recording':trace.trip.metrics.recording});
            curve.append(svgNode('title',{},trace.trip.metrics.display_recording || trace.trip.metrics.recording));curves.append(curve);
          }
        }
        svg.append(curves);
        if(!visible.length)svg.append(svgNode('text',{x:(left+right)/2,y:(top+bottom)/2,'text-anchor':'middle'},'Select a recording below to display its signal.'));
        svg.append(svgNode('text',{x:left,y:18},'Filtered forward acceleration (m/s²)'));
        svg.append(svgNode('text',{x:(left+right)/2,y:395,'text-anchor':'middle'},'Seconds since restart time'));
      }
      inputs.forEach(input=>input.onchange=()=>{
        const from=Number(inputs[0].value),to=Number(inputs[1].value);
        if(inputs.some(i=>i.value==='' || !i.checkValidity()) || to<=from){draw();return;}
        view(from,to);
      });
      button('Zoom in',()=>zoom(.5));button('Zoom out',()=>zoom(2));
      button('First 30 s',()=>view(0,Math.min(30,duration)));button('Full runs',()=>view(0,duration));
      const eventTime=event=>{
        const box=svg.getBoundingClientRect(), px=(event.clientX-box.left)/box.width*1100;
        return Math.max(lo,Math.min(hi,lo+(px-left)/(right-left)*(hi-lo)));
      };
      svg.addEventListener('wheel',event=>{event.preventDefault();zoom(event.deltaY < 0 ? 0.8 : 1.25,eventTime(event));},{passive:false});
      svg.onpointerdown=event=>{if(event.button!==0)return;anchor=eventTime(event);svg.setPointerCapture(event.pointerId);};
      svg.onpointerup=event=>{if(anchor===null)return;const from=anchor,to=eventTime(event);anchor=null;if(Math.abs(to-from)>.1)view(Math.min(from,to),Math.max(from,to));};
      svg.onpointercancel=()=>{anchor=null;};
      svg.onkeydown=event=>{
        if(event.key==='+' || event.key==='='){event.preventDefault();zoom(.5);}
        if(event.key==='-'){event.preventDefault();zoom(2);}
        if(event.key==='Home'){event.preventDefault();view(0,duration);}
      };
      draw();
    }
    select.onchange=show;show();
  };
})();
