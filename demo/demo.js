/* Snapshot details and lazily loaded comparisons; no server or third-party scripts. */
(async () => {
  try {
    const response = await fetch('snapshot.json');
    if (!response.ok) return;
    const data = await response.json();
    document.getElementById('departure-window').textContent = `${data.restart_offset_seconds}–${data.restart_offset_seconds + data.departure_window_seconds} s after validated restart`;
    document.getElementById('arrival-window').textContent = `Last ${data.arrival_window_seconds} s before validated stop`;
    document.getElementById('snapshot-description').textContent = `${data.recordings} recordings; ${data.runs} interstation runs.`;
  } catch { /* The static chart remains usable without snapshot metadata. */ }
})();
(() => {
  const root=document.getElementById('run-comparison-charts');
  if(!root)return;
  async function loadComparisons(){
    root.textContent='Loading run comparisons…';
    try{
      const response=await fetch('comparisons.json');
      if(!response.ok)throw Error('Comparison data unavailable');
      const data=await response.json();
      window.renderRunComparisons(data.trips,{linkRuns:false});
    }catch{
      root.textContent='Run comparisons could not be loaded. ';
      const retry=document.createElement('button');retry.type='button';retry.textContent='Try again';retry.onclick=loadComparisons;root.append(retry);
    }
  }
  if('IntersectionObserver' in window){
    const observer=new IntersectionObserver(entries=>{
      if(entries.some(entry=>entry.isIntersecting)){observer.disconnect();loadComparisons();}
    },{rootMargin:'200px'});
    observer.observe(root);
  }else loadComparisons();
})();
