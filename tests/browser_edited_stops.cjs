// Optional regression check: edited stop bounds must match canvas shading and table labels.
const {chromium}=require(process.env.METRO_PLAYWRIGHT_DIR || 'playwright');
const {spawn,spawnSync}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const cwd=path.resolve(__dirname,'..'),temporary=fs.mkdtempSync(path.join(os.tmpdir(),'metro-edited-stops-'));
 const output=path.join(temporary,'results');fs.mkdirSync(output);
 const recording={recording:'fixture',start_s:.5,end_s:353.2,expected_intermediate_stops:1,detected_interior_stops:1,short_dwell_candidates:1,gap_count:0};
 const candidate=(id,start,end,automatic)=>({id,recording:'fixture',start_s:start,end_s:end,duration_s:end-start,automatic,boundary:'interior'});
 const data={version:1,minimum_stop_seconds:15,recordings:[recording],
  candidates:[candidate('four',260.474,274.024,false),candidate('five',277.524,297.774,true)],series:{fixture:{}},trips:[]};
 fs.writeFileSync(path.join(output,'report_data.json'),JSON.stringify(data));
 const generated=spawnSync(process.env.METRO_PYTHON || 'python',['-c',
  'import json,sys;from metro_braking.cli import route_checks_table;p=json.load(sys.stdin);print(route_checks_table(p["recordings"],p["candidates"]))'],{cwd,input:JSON.stringify(data),encoding:'utf8'});
 assert.equal(generated.status,0,generated.stderr);
 fs.writeFileSync(path.join(output,'report.html'),`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="report_ui.css"><div id="review-save-status"></div><button id="download-stops">Download reviewed stops CSV</button><button id="export-sections">Export sections</button><a id="section-index" hidden></a>${generated.stdout}<div class="stop-review" data-review-recording="0"></div><script id="review-data" type="application/json">${JSON.stringify(data)}</script><script src="trip_charts.js"></script><script src="report_ui.js"></script>`);
 for(const name of ['report_ui.css','report_ui.js','trip_charts.js'])fs.copyFileSync(path.join(cwd,'metro_braking',name),path.join(output,name));
 const server=spawn(process.env.METRO_PYTHON || 'python',['-m','metro_braking','--serve','--output',output,'--review-dir',path.join(temporary,'reviews'),'--port','8783'],{cwd});
 let logs='',browser;server.stdout.on('data',x=>logs+=x);server.stderr.on('data',x=>logs+=x);
 try{
  for(let i=0;i<100&&!logs.includes('Review stops at');i++){if(server.exitCode!==null)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
  assert(logs.includes('Review stops at'),logs);
  browser=await chromium.launch({headless:true,executablePath:process.env.METRO_BROWSER_EXE});
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const url='http://127.0.0.1:8783/report.html';await page.goto(url);
  const status=page.locator('#review-save-status'),four=page.locator('[data-candidate-id="four"]'),five=page.locator('[data-candidate-id="five"]');
  await status.filter({hasText:'Disk save connected'}).waitFor();
  const routeRow=page.locator('.route-checks-table [data-route-recording="0"]'),acceptedCount=routeRow.locator('[data-field="accepted_interior_stops"]');
  const reviewStatus=routeRow.locator('[data-field="review_status"]');
  assert.equal(await reviewStatus.textContent(),'Pending decisions (2)');
  assert.equal(await page.locator('.stop-review .review-badge').textContent(),'Pending decisions (2)');
  assert.equal(await acceptedCount.textContent(),'0');
  assert((await routeRow.getAttribute('class')).includes('stop-count-mismatch'));
  assert((await routeRow.textContent()).includes('2 pending'));
  const canvas=page.locator('canvas');
  const pixel=time=>canvas.evaluate((canvas,time)=>{
   const width=Math.max(400,canvas.clientWidth),lo=Number(canvas.dataset.viewStart),hi=Number(canvas.dataset.viewEnd);
   const x=Math.round(55+(time-lo)/(hi-lo)*(width-70));
   return [...canvas.getContext('2d').getImageData(x,155,1,1).data].slice(0,3);
  },time);
  assert.deepEqual(await pixel(276),[255,255,255]);
  await four.locator('.candidate-focus').click();
  assert(Math.abs(Number(await canvas.getAttribute('data-view-end'))-294.024)<1e-8);
  const restart=four.getByLabel('Restart time',{exact:true});await restart.fill('297.774');await restart.press('Tab');
  await status.filter({hasText:'Saved to reviews'}).waitFor();
  assert(Math.abs(Number(await canvas.getAttribute('data-view-end'))-317.774)<1e-8);
  assert((await four.locator('.candidate-focus').textContent()).includes('260.5–297.8'));
  assert.equal(await four.locator('.candidate-duration').textContent(),'37.3s');
  assert.deepEqual(await pixel(276),[255,240,194]);
  await four.getByRole('button',{name:'Accept',exact:true}).click();await status.filter({hasText:'Saved to reviews'}).waitFor();
  assert.deepEqual(await pixel(276),[209,234,216]);
  assert.equal(await acceptedCount.textContent(),'1');assert(!(await routeRow.getAttribute('class')).includes('stop-count-mismatch'));
  assert((await routeRow.textContent()).includes('1 pending'));
  assert.equal(await reviewStatus.textContent(),'Pending decisions (1)');
  assert.deepEqual(await pixel(290),[209,234,216]); // Pending stop 5 cannot hide the edited accepted interval.
  await five.getByRole('button',{name:'Discard',exact:true}).click();await status.filter({hasText:'Saved to reviews'}).waitFor();
  assert.deepEqual(await pixel(290),[209,234,216]); // Discarded stop 5 cannot paint over it either.
  const arrival=four.getByLabel('Arrival time',{exact:true});await arrival.fill('258');await arrival.press('Tab');await status.filter({hasText:'Saved to reviews'}).waitFor();
  assert(Math.abs(Number(await canvas.getAttribute('data-view-start'))-238)<1e-8);
  assert.equal(await four.locator('.candidate-duration').textContent(),'39.8s');
  await page.reload();await status.filter({hasText:'Disk save connected'}).waitFor();
  assert((await four.locator('.candidate-focus').textContent()).includes('258.0–297.8'));
  assert((await four.locator('.candidate-focus').getAttribute('title')).includes('260.474–274.024'));
  assert.deepEqual(await pixel(259),[209,234,216]);
  assert.equal(await acceptedCount.textContent(),'1');
  assert((await routeRow.textContent()).includes('0 pending'));
  assert.equal(await reviewStatus.textContent(),'Review complete');
  assert.equal(await page.locator('.stop-review .review-badge').textContent(),'Review complete');
  await five.getByRole('button',{name:'Pending',exact:true}).click();await status.filter({hasText:'Saved to reviews'}).waitFor();
  assert.equal(await reviewStatus.textContent(),'Pending decisions (1)');
  await five.getByRole('button',{name:'Discard',exact:true}).click();await status.filter({hasText:'Saved to reviews'}).waitFor();
  assert.equal(await reviewStatus.textContent(),'Review complete');
  await page.getByRole('button',{name:'Add manual stop',exact:true}).click();
  await page.getByLabel('Manual arrival time',{exact:true}).fill('100');await page.getByLabel('Manual restart time',{exact:true}).fill('110');
  await page.getByRole('button',{name:'Add and accept stop',exact:true}).click();await status.filter({hasText:'Saved to reviews'}).waitFor();
  assert.equal(await acceptedCount.textContent(),'2');assert((await routeRow.getAttribute('class')).includes('stop-count-mismatch'));
  await page.locator('tr[data-candidate-id^="manual-"]').getByRole('button',{name:'Discard',exact:true}).click();await status.filter({hasText:'Saved to reviews'}).waitFor();
  assert.equal(await acceptedCount.textContent(),'1');assert(!(await routeRow.getAttribute('class')).includes('stop-count-mismatch'));
  const stored=JSON.parse(fs.readFileSync(path.join(temporary,'reviews/stops.json'),'utf8'));
  assert.equal(stored.decisions.four.time_s,258);assert.equal(stored.decisions.four.restart_s,297.774);assert.equal(stored.decisions.five.decision,'discarded');
  await page.route('**/api/reviews',r=>r.fulfill({status:404,contentType:'application/json',body:'{}'}));
  await page.reload();await status.filter({hasText:'Browser-only mode'}).waitFor();
  assert.deepEqual(await pixel(290),[209,234,216]);
  assert.equal(await four.locator('.candidate-duration').textContent(),'39.8s');
  assert.equal(await acceptedCount.textContent(),'1');assert(!(await routeRow.getAttribute('class')).includes('stop-count-mismatch'));
  assert.deepEqual(errors,[]);
  console.log('PASS: edited arrival/restart shading, live zoom and duration labels, overlap layering, original-detection tooltip, disk/browser reload persistence, live route counts/status/color including manual stops; no JavaScript errors.');
 }finally{if(browser)await browser.close();if(server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.rmSync(temporary,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
