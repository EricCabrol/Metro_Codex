(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('review-data').textContent);
  const key = 'metro-stop-decisions-v1';
  let decisions = {}, server = false, saveQueue = Promise.resolve(), recordingMutation = false;
  const status = document.getElementById('review-save-status');
  const detected = data.candidates.filter(c => !c.manual);
  const all = new Map(data.candidates.map(c => [c.id, c]));
  function manualCandidate(id, value) {
    if (!/^manual-[a-zA-Z0-9-]{1,80}$/.test(id)) throw Error('Invalid manual stop identifier.');
    const recording = data.recordings.find(r=>r.recording===value.recording);
    const start=value.time_s, end=value.restart_s;
    if (!recording || !Number.isFinite(start) || !Number.isFinite(end) || start<recording.start_s || end>recording.end_s || end<=start) throw Error('Manual stop requires arrival < restart inside the recording.');
    const left=start<=recording.start_s+0.1, right=end>=recording.end_s-0.1;
    return {id,recording:value.recording,start_s:start,end_s:end,duration_s:end-start,manual:true,automatic:false,boundary:left||right?'recording edge':'interior',left_censored:left,right_censored:right};
  }
  function restoreManual(values) {
    data.candidates=[...detected]; all.clear(); detected.forEach(c=>all.set(c.id,c));
    for (const [id,value] of Object.entries(values)) {
      if (!id.startsWith('manual-') || value?.manual !== true) continue;
      try {const c=manualCandidate(id,value);data.candidates.push(c);all.set(id,c);} catch { /* Ignore invalid browser-only definitions. */ }
    }
  }
  const state = c => ({decision: 'pending', time_s: c.start_s, restart_s: c.end_s, note: '', ...decisions[c.id]});
  const element = (tag, text, className) => {
    const e = document.createElement(tag); if (text !== undefined) e.textContent = text;
    if (className) e.className = className; return e;
  };
  function browserSave() {
    try {localStorage.setItem(key, JSON.stringify(decisions)); return true;}
    catch {return false;}
  }
  async function request(path, body) {
    const response = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Save failed');
    return result;
  }
  function save() {
    document.getElementById('section-index').hidden=true;
    const local = browserSave();
    if (!server) {status.textContent = local ? 'Saved in this browser. Download CSV to use these stops in Python.' : 'Browser storage unavailable. Download CSV to keep decisions.'; return;}
    const snapshot = JSON.parse(JSON.stringify(decisions));
    status.textContent = 'Saving…';
    saveQueue = saveQueue.catch(() => {}).then(() => request('/api/reviews', {decisions: snapshot})).then(() => {
      status.textContent = 'Saved to reviews/stops.json and stops.csv. Export sections to refresh station labels and duration checks.';
    }).catch(error => {status.textContent = 'Disk save failed: ' + error.message + '. Decisions remain in browser storage.'; throw error;});
    // Keep failures visible without an unhandled rejection.
    saveQueue.catch(() => {});
  }
  function updateCount(card, recording) {
    const own = data.candidates.filter(c => c.recording === recording.recording);
    const accepted = own.filter(c => state(c).decision === 'accepted');
    const interior = accepted.filter(c => c.boundary === 'interior').length;
    const pending = own.filter(c => state(c).decision === 'pending').length;
    card.querySelector('.review-count').textContent = `${accepted.length} accepted (${interior} interior), ${pending} pending. Expected intermediate stops: ${recording.expected_intermediate_stops ?? 'unknown'}.${accepted.length && interior === recording.expected_intermediate_stops && !recording.gap_count && !accepted.some(c=>c.boundary==='gap edge') ? ' Station names inferred from stop order.' : ''}`;
  }
  function draw(canvas, recording, selected, zoom, selection=null) {
    const width = Math.max(400, canvas.clientWidth), height = 300;
    const scale = window.devicePixelRatio || 1;
    canvas.width = width * scale; canvas.height = height * scale;
    const ctx = canvas.getContext('2d'); ctx.scale(scale, scale);
    const left = 55, right = width - 15, top = 20, bottom = 265;
    const span = selected ? selected.end_s - selected.start_s : 0;
    const lo = selected && zoom ? Math.max(recording.start_s, selected.start_s - 20) : recording.start_s;
    const hi = selected && zoom ? Math.min(recording.end_s, selected.end_s + 20) : recording.end_s;
    canvas.dataset.viewStart = lo; canvas.dataset.viewEnd = hi;
    const x = t => left + (t - lo) / (hi - lo) * (right - left);
    const traces = data.series[recording.recording] || {};
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, width, height);
    for (const c of data.candidates.filter(c => c.recording === recording.recording)) {
      if (c.end_s < lo || c.start_s > hi) continue;
      const decision = state(c).decision;
      ctx.fillStyle = decision === 'accepted' ? '#d1ead8' : decision === 'discarded' ? '#f4dddd' : '#fff0c2';
      ctx.fillRect(x(Math.max(lo,c.start_s)), top, x(Math.min(hi,c.end_s)) - x(Math.max(lo,c.start_s)), bottom - top);
    }
    function trace(blocks, field, ylo, yhi, ytop, ybottom, color) {
      const y = v => ybottom - (Math.max(ylo,Math.min(yhi,v)) - ylo) / (yhi-ylo) * (ybottom-ytop);
      ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.beginPath();
      for (const block of blocks || []) {
        let started = false;
        for (const row of block) {
          if (row[0] < lo || row[0] > hi || row[field] === null) {started=false; continue;}
          if (!started) {ctx.moveTo(x(row[0]), y(row[field])); started=true;} else ctx.lineTo(x(row[0]), y(row[field]));
        }
      }
      ctx.stroke();
      ctx.strokeStyle='#d4d8dc'; ctx.beginPath(); ctx.moveTo(left,y(0)); ctx.lineTo(right,y(0)); ctx.stroke();
      ctx.fillStyle='#52606d'; ctx.font='11px system-ui'; ctx.fillText(yhi.toFixed(1),8,ytop+8); ctx.fillText(ylo.toFixed(1),8,ybottom);
    }
    const values = (traces.acceleration || []).flat().filter(r=>r[0]>=lo && r[0]<=hi).map(r=>Math.abs(r[1]));
    const limit = Math.max(1.5, Math.min(6, Math.max(...values, 0)*1.1));
    trace(traces.acceleration, 1, -limit, limit, 30, 140, '#1c3544');
    trace(traces.stops, 1, 0, 0.6, 185, 260, '#16855a');
    trace(traces.stops, 2, 0, 0.6, 185, 260, '#80909d');
    ctx.fillStyle='#17202a'; ctx.fillText('Forward acceleration (m/s²)',left,15);
    ctx.fillText('Stop variation (green) / baseline distance (gray), m/s² — clipped at 0.6',left,173);
    ctx.fillStyle='#52606d'; for(let i=0;i<=6;i++) {const t=lo+(hi-lo)*i/6; ctx.fillText(t.toFixed(1)+'s',x(t)-10,285);}
    if (selection) {
      ctx.fillStyle='#187bb633'; ctx.strokeStyle='#187bb6';ctx.lineWidth=2;
      const a=x(Math.min(...selection)), b=x(Math.max(...selection));
      ctx.fillRect(a,top,b-a,bottom-top);ctx.strokeRect(a,top,b-a,bottom-top);
    }
    if (selected) {
      ctx.strokeStyle='#0867a5'; ctx.lineWidth=2; ctx.beginPath(); ctx.moveTo(x(state(selected).time_s),top); ctx.lineTo(x(state(selected).time_s),bottom); ctx.stroke();
    }
  }
  function mount(recording) {
    const card = document.querySelector(`[data-review-recording="${data.recordings.indexOf(recording)}"]`);
    if (!card) return;
    const own = data.candidates.filter(c=>c.recording===recording.recording).sort((a,b)=>a.start_s-b.start_s);
    const archiveButton=element('button','Archive recording');archiveButton.type='button';
    archiveButton.disabled=!server || !data.data_directory;
    archiveButton.title='Move the source folder to the archive and rebuild the report. Restore it from Archived recordings.';
    archiveButton.onclick=()=>changeRecording(recording.recording,'archive',archiveButton);
    card.append(archiveButton);
    const heading = element('h3','Review station stops'); card.append(heading);
    card.append(element('p','Select a candidate to inspect its surrounding signal. Accept confirms a station stop; discard excludes it. Short candidates can be accepted after inspection. Restart defaults to the end of the accepted stop; adjust it if necessary.'));
    card.append(element('p','', 'review-count'));
    let selected = own[0] || null, zoom = false, selection=null;
    const toolbar = element('div', undefined, 'review-toolbar');
    const zoomButton = element('button','Zoom to selected stop'); zoomButton.type='button';
    toolbar.append(zoomButton); card.append(toolbar);
    const canvas = element('canvas',undefined,'review-chart'); canvas.setAttribute('aria-label','Acceleration and stop evidence around selected candidate'); card.append(canvas);
    const manualButton=element('button','Add manual stop');manualButton.type='button';toolbar.append(manualButton);
    const form=element('form',undefined,'manual-stop-form');form.hidden=true;
    form.append(element('p','Drag across the chart to select arrival and restart, or enter their recording timestamps below. Save only after checking both boundaries.'));
    const manualInputs=[];
    for(const label of ['Manual arrival time','Manual restart time']) {
      const wrapper=element('label',label+' (s) '), input=element('input');
      input.type='number';input.step='any';input.required=true;input.min=recording.start_s;input.max=recording.end_s;input.setAttribute('aria-label',label);
      wrapper.append(input);form.append(wrapper);manualInputs.push(input);
    }
    const noteLabel=element('label','Note '), manualNote=element('input');manualNote.type='text';manualNote.maxLength=2000;manualNote.setAttribute('aria-label','Manual stop note');noteLabel.append(manualNote);form.append(noteLabel);
    const submit=element('button','Add and accept stop');submit.type='submit';form.append(submit);
    const cancel=element('button','Cancel');cancel.type='button';form.append(cancel);
    const error=element('p',undefined,'manual-stop-error');error.setAttribute('role','alert');form.append(error);card.insertBefore(form,canvas);
    const closeForm=()=>{form.hidden=true;canvas.classList.remove('selecting-stop');selection=null;anchor=null;error.textContent='';refresh();};
    manualButton.onclick=()=>{form.hidden=false;canvas.classList.add('selecting-stop');error.textContent='';manualInputs[0].focus();};
    cancel.onclick=closeForm;
    let anchor=null;
    const chartTime=event=>{
      const box=canvas.getBoundingClientRect(), width=Math.max(400,canvas.clientWidth);
      const fraction=Math.max(0,Math.min(1,((event.clientX-box.left)/box.width*width-55)/(width-70)));
      return Number(canvas.dataset.viewStart)+fraction*(Number(canvas.dataset.viewEnd)-Number(canvas.dataset.viewStart));
    };
    canvas.onpointerdown=event=>{if(form.hidden||event.button!==0)return;anchor=chartTime(event);selection=[anchor,anchor];canvas.setPointerCapture(event.pointerId);refresh();};
    canvas.onpointermove=event=>{if(anchor!==null){selection=[anchor,chartTime(event)];refresh();}};
    canvas.onpointerup=event=>{
      if(anchor===null)return;const end=chartTime(event);selection=[Math.min(anchor,end),Math.max(anchor,end)];anchor=null;
      manualInputs.forEach((input,i)=>input.value=String(Math.max(recording.start_s,Math.min(recording.end_s,Math.round(selection[i]*1000)/1000))));refresh();
    };
    canvas.onpointercancel=()=>{anchor=null;selection=null;refresh();};
    const table = element('table',undefined,'review-table');
    const header = element('tr'); ['Candidate','Duration','Boundary','Decision','Arrival time (s)','Restart time (s)','Note'].forEach(t=>header.append(element('th',t))); table.append(header);
    const body = element('tbody'); table.append(body); card.append(table);
    const refresh = () => {
      for(const c of own) if(c.manual) Object.assign(c,manualCandidate(c.id,state(c)));
      updateCount(card,recording); draw(canvas,recording,selected,zoom,selection);
      for(const c of [...own].sort((a,b)=>state(a).time_s-state(b).time_s)) {
        const row=body.querySelector(`[data-candidate-id="${c.id}"]`);
        if(row){body.append(row);row.dataset.selected=String(selected?.id===c.id);
          if(c.manual){row.querySelector('.candidate-focus').textContent=`${c.start_s.toFixed(1)}–${c.end_s.toFixed(1)}s · manual`;row.querySelector('.candidate-duration').textContent=c.duration_s.toFixed(1)+'s';row.querySelector('.candidate-boundary').textContent=c.boundary;}}
      }
    };
    zoomButton.onclick=()=>{zoom=!zoom; zoomButton.textContent=zoom?'Show full recording':'Zoom to selected stop'; refresh();};
    function appendCandidate(c) {
      const row=element('tr'); row.dataset.candidateId=c.id;
      const cell=element('td'); const focus=element('button',`${c.start_s.toFixed(1)}–${c.end_s.toFixed(1)}s`); focus.type='button';focus.className='candidate-focus';
      focus.onclick=()=>{selected=c;zoom=true;zoomButton.textContent='Show full recording'; refresh();}; cell.append(focus);row.append(cell);
      row.append(element('td',c.duration_s.toFixed(1)+'s'+(c.manual?' · manual':c.automatic?'':' · short'),'candidate-duration'));
      row.append(element('td',c.boundary,'candidate-boundary'));
      const controls=element('td',undefined,'decision-controls');
      function redraw() {row.dataset.decision=state(c).decision; controls.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.decision===state(c).decision)));}
      for(const decision of ['accepted','discarded','pending']) {
        const button=element('button',({accepted:'Accept',discarded:'Discard',pending:'Pending'})[decision]);button.type='button';button.dataset.decision=decision;
        button.onclick=()=>{decisions[c.id]={...state(c),decision};selected=c;redraw();refresh();save();};controls.append(button);
      }
      row.append(controls);
      const timeCell=element('td');const time=element('input');time.type='number';time.step='any';time.min=recording.start_s;time.max=recording.end_s;time.value=state(c).time_s.toFixed(3);time.setAttribute('aria-label','Arrival time');
      time.onchange=()=>{if(!time.checkValidity()||time.value===''){time.reportValidity();return;}if(Number(time.value)>state(c).restart_s || c.manual && Number(time.value)===state(c).restart_s){time.value=state(c).time_s;return;}decisions[c.id]={...state(c),time_s:Number(time.value)};selected=c;refresh();save();};timeCell.append(time);row.append(timeCell);
      const restartCell=element('td');const restart=element('input');restart.type='number';restart.step='any';restart.min=state(c).time_s;restart.max=recording.end_s;restart.value=state(c).restart_s.toFixed(3);restart.setAttribute('aria-label','Restart time');
      restart.onchange=()=>{restart.min=state(c).time_s;if(!restart.checkValidity()||restart.value===''||c.manual&&Number(restart.value)<=state(c).time_s){restart.reportValidity();return;}decisions[c.id]={...state(c),restart_s:Number(restart.value)};selected=c;refresh();save();};restartCell.append(restart);row.append(restartCell);
      const noteCell=element('td');const note=element('input');note.type='text';note.maxLength=2000;note.value=state(c).note;note.setAttribute('aria-label','Stop note');note.onchange=()=>{decisions[c.id]={...state(c),note:note.value};save();};noteCell.append(note);row.append(noteCell);
      body.append(row);redraw();
    }
    own.forEach(appendCandidate);
    form.onsubmit=event=>{
      event.preventDefault();error.textContent='';
      if(!form.reportValidity())return;
      const id='manual-'+(globalThis.crypto?.randomUUID?.() || Date.now().toString(16)+'-'+Math.random().toString(16).slice(2));
      const value={manual:true,recording:recording.recording,time_s:Number(manualInputs[0].value),restart_s:Number(manualInputs[1].value),note:manualNote.value,decision:'accepted'};
      let candidate;
      try{
        candidate=manualCandidate(id,value);
        const conflict=own.some(c=>state(c).decision==='accepted' && value.time_s<=state(c).restart_s && value.restart_s>=state(c).time_s);
        if(conflict)throw Error('This interval overlaps an accepted stop. Edit or discard that stop first.');
      }catch(e){error.textContent=e.message;return;}
      decisions[id]=value;data.candidates.push(candidate);all.set(id,candidate);own.push(candidate);appendCandidate(candidate);selected=candidate;
      closeForm();save();form.reset();
    };
    if (!own.length) card.append(element('p','No detected candidates. You can add a manual stop.'));
    new ResizeObserver(refresh).observe(canvas);refresh();
  }
  function downloadCSV() {
    const quote = v => '"'+String(v).replaceAll('"','""')+'"';
    const rows=[['recording','time_s','restart_s','candidate_id','accepted','note','manual']];
    for(const c of data.candidates) {const s=state(c);if(s.decision!=='pending') rows.push([c.recording,s.time_s,s.restart_s,c.id,s.decision==='accepted',s.note,!!c.manual]);}
    const link=element('a');const url=URL.createObjectURL(new Blob([rows.map(row=>row.map(quote).join(',')).join('\r\n')+'\r\n'],{type:'text/csv;charset=utf-8'}));
    link.href=url;link.download='stops.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  window.openReportAnchor = () => {
    let id;
    try {id = decodeURIComponent(location.hash.slice(1));} catch {return;}
    const target = document.getElementById(id);
    if (!target) return;
    for (let node = target; node; node = node.parentElement) {
      if (node.tagName === 'DETAILS') node.open = true;
    }
    requestAnimationFrame(() => target.scrollIntoView({block:'start'}));
  };
  window.addEventListener('hashchange', window.openReportAnchor);
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (link && link.hash === location.hash) window.openReportAnchor();
  });
  const toolbar = document.querySelector('.review-global');
  if (toolbar) new ResizeObserver(() => {
    document.documentElement.style.setProperty('--report-scroll-offset', `${toolbar.getBoundingClientRect().height+16}px`);
  }).observe(toolbar);
  async function changeRecording(recording, action, button) {
    if (recordingMutation) return;
    if (action==='archive' && !confirm('Archive this recording? Its source folder will be moved to the archive. Reviews are kept and you can restore it below.')) return;
    button.disabled=true;
    recordingMutation=true;document.body.inert=true;
    status.textContent='Moving recording and rebuilding report…';
    try {await saveQueue;await request('/api/recordings',{recording,action});location.reload();}
    catch(error){status.textContent=error.message;button.disabled=false;recordingMutation=false;document.body.inert=false;}
  }
  async function loadArchived() {
    const list=document.getElementById('archive-list');if(!list || !server)return;
    try {
      const result=await request('/api/recordings');list.replaceChildren();
      if(!result.enabled){list.textContent='Regenerate the report once to enable recording archiving.';return;}
      if(!result.archived.length)list.textContent='No archived recordings.';
      for(const entry of result.archived){
        const row=element('p',entry.display_name+' '),button=element('button','Restore recording');
        button.onclick=()=>changeRecording(entry.recording,'restore',button);row.append(button);list.append(row);
      }
    }catch(error){list.textContent=error.message;}
  }
  async function init() {
    try {decisions={...(data.initial_decisions||{}),...JSON.parse(localStorage.getItem(key)||'{}')};} catch {decisions={...(data.initial_decisions||{})};}
    restoreManual(decisions);
    // Ignore old boundaries; candidate IDs are stable across threshold reruns.
    decisions=Object.fromEntries(Object.entries(decisions).filter(([id])=>all.has(id)));
    if (location.protocol.startsWith('http')) {
      try {const result=await request('/api/reviews');decisions=result.decisions;restoreManual(decisions);server=true;status.textContent='Disk save connected. Decisions persist in reviews/stops.json.';}
      catch {status.textContent='Browser-only mode. Download CSV to keep a portable copy.';}
    } else status.textContent='Browser-only mode. Choices persist here; Download CSV exports reviewed stops.';
    data.recordings.forEach(mount);
    await loadArchived();
    window.renderTripCharts(data.trips || []);
    document.getElementById('download-stops').onclick=downloadCSV;
    const exportButton=document.getElementById('export-sections');exportButton.disabled=!server;
    exportButton.onclick=async()=>{
      exportButton.disabled=true;
      try {await saveQueue;const result=await request('/api/export-sections',{});status.textContent=`Exported ${result.sections} sections and compared ${result.complete_windows} complete ${result.window_seconds}-second acceleration/deceleration windows. At least two accepted stops per recording are required.`;
        const link=document.getElementById('section-index');link.href=result.index;link.hidden=false;
        const image=document.querySelector('img[src^="trip_acceleration.png"]');
        if(image) image.src=result.acceleration_plot+'?updated='+Date.now();
        const deceleration=document.querySelector('img[src^="trip_deceleration.png"]');
        if(deceleration) deceleration.src=result.deceleration_plot+'?updated='+Date.now();
        const globalPlot=document.getElementById('global-statistics-plot');
        if(globalPlot && result.global_statistics_plot)globalPlot.src=result.global_statistics_plot+'?updated='+Date.now();
        window.renderTripCharts(result.trips || []);}
      catch(error){status.textContent='Section export failed: '+error.message;}
      finally {exportButton.disabled=false;}
    };
  }
  init();
})();
