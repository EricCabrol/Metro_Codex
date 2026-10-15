/* Optional snapshot details; the chart and CSV also work without JavaScript. */
(async () => {
  try {
    const response = await fetch('snapshot.json');
    if (!response.ok) return;
    const data = await response.json();
    document.getElementById('departure-window').textContent = `${data.restart_offset_seconds}–${data.restart_offset_seconds + data.departure_window_seconds} s after validated restart`;
    document.getElementById('arrival-window').textContent = `Last ${data.arrival_window_seconds} s before validated stop`;
    document.getElementById('snapshot-description').textContent = `${data.recordings} recordings; ${data.runs} interstation runs.`;
  } catch { /* Keep the static chart usable when snapshot metadata is unavailable. */ }
})();
